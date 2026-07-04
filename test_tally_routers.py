import uuid
from datetime import date
import sys
from unittest.mock import MagicMock
sys.modules["weasyprint"] = MagicMock()

from fastapi.testclient import TestClient
from app.main import app
import pytest
from unittest.mock import patch

client = TestClient(app)

class FakeResponse:
    def __init__(self, data):
        self.data = data
    
    def execute(self):
        return self

class FakeTable:
    def __init__(self, fake_db, table_name):
        self.db = fake_db
        self.table = table_name
        self._filters = {}
        self._in_filters = {}
        self._neq_filters = {}
    
    def insert(self, data):
        if not isinstance(data, list):
            data = [data]
        self.db.storage[self.table] = self.db.storage.get(self.table, [])
        for d in data:
            if "id" not in d:
                d["id"] = "fake_id_123"
            self.db.storage[self.table].append(d)
        
        self.db.inserts.extend([(self.table, d) for d in data])
        return FakeResponse(data)
    
    def update(self, data, **kwargs):
        self.db.updates.append((self.table, data))
        return self

    def upsert(self, data, **kwargs):
        return self.insert(data)
    
    def select(self, *args):
        return self
    
    def eq(self, field, value):
        self._filters[field] = value
        return self

    def in_(self, field, values):
        self._in_filters[field] = values
        return self

    def neq(self, field, value):
        self._neq_filters[field] = value
        return self

    def order(self, *args, **kwargs):
        return self
    
    def execute(self):
        results = self.db.storage.get(self.table, [])
        for field, value in self._filters.items():
            results = [r for r in results if r.get(field) == value]
        for field, values in self._in_filters.items():
            results = [r for r in results if r.get(field) in values]
        for field, value in self._neq_filters.items():
            results = [r for r in results if r.get(field) != value]
        return FakeResponse(results)

class FakeDB:
    def __init__(self):
        self.storage = {
            "businesses": [{"id": "some_id", "agent_token": "valid_token"}],
            "clients": [],
            "bills": [],
            "tally_syncs": []
        }
        self.inserts = []
        self.updates = []
    
    def table(self, name):
        return FakeTable(self, name)

@pytest.fixture
def fake_db():
    db = FakeDB()
    with patch("app.routers.tally.require_db", return_value=db):
        yield db

def test_import_outstanding(fake_db):
    biz_id = str(uuid.uuid4())
    fake_db.storage["businesses"] = [{"id": biz_id, "agent_token": "valid_token"}]
    payload = {
        "business_id": biz_id,
        "agent_token": "valid_token",
        "company_name": "TEST",
        "debtors": [
            {"name": "Positive Debtor", "opening_balance": 1000.0, "tally_group": "Group1"},
            {"name": "Negative Debtor", "opening_balance": -500.0, "tally_group": "Group1"},
            {"name": "Zero Debtor", "opening_balance": 0.0, "tally_group": "Group1"},
        ]
    }
    
    resp = client.post("/tally/import", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["errors"] == []
    assert data["clients_created"] == 3
    assert data["credit_balances"] == 1
    assert data["zero_balances"] == 1
    
    # Check bills inserted (only 1 for positive debtor)
    bill_inserts = [i[1] for i in fake_db.inserts if i[0] == "bills"]
    assert len(bill_inserts) == 1
    assert bill_inserts[0]["amount"] == 1000.0
    assert bill_inserts[0]["is_opening_balance"] == True
    assert bill_inserts[0]["tally_voucher_number"] == "OB-Positive Debtor"

def test_sync_duplicate_voucher(fake_db):
    biz_id = str(uuid.uuid4())
    fake_db.storage["businesses"] = [{"id": biz_id, "agent_token": "valid_token"}]
    # Setup client exists
    fake_db.storage["clients"] = [{"id": "client_1", "business_id": biz_id, "whatsapp_number": "123", "credit_days": 30, "tally_ledger_name": "Test Party"}]
    # Setup bill already exists
    fake_db.storage["bills"] = [{"id": "bill_1", "business_id": biz_id, "tally_voucher_number": "V-123", "amount": 100}]

    payload = {
        "business_id": biz_id,
        "agent_token": "valid_token",
        "company_name": "TEST",
        "sync_date": "2026-06-13",
        "vouchers": [
            {"voucher_number": "V-123", "voucher_type": "Sales", "party_name": "Test Party", "amount": 500.0, "date": "2026-06-13"}
        ]
    }
    
    with patch("app.routers.tally.BackgroundTasks.add_task") as mock_bg:
        resp = client.post("/tally/sync", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["sales_processed"] == 1
        
        # Verify it was an UPSERT (update), not insert
        bill_inserts = [i for i in fake_db.inserts if i[0] == "bills"]
        bill_updates = [i for i in fake_db.updates if i[0] == "bills"]
        
        assert len(bill_inserts) == 0
        assert len(bill_updates) == 1
        assert bill_updates[0][1]["amount"] == 500.0
        # Should not trigger whatsapp for duplicate
        mock_bg.assert_not_called()

def test_sync_unmatched_party(fake_db):
    biz_id = str(uuid.uuid4())
    fake_db.storage["businesses"] = [{"id": biz_id, "agent_token": "valid_token"}]
    # Setup client does not exist
    fake_db.storage["clients"] = []

    payload = {
        "business_id": biz_id,
        "agent_token": "valid_token",
        "company_name": "TEST",
        "sync_date": "2026-06-13",
        "vouchers": [
            {"voucher_number": "V-124", "voucher_type": "Sales", "party_name": "Ghost Party", "amount": 500.0, "date": "2026-06-13"}
        ]
    }
    
    resp = client.post("/tally/sync", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sales_processed"] == 0
    assert "Ghost Party" in data["unmatched_parties"]
    
    # Check that it logged to tally_syncs with errors (schema: error text, success bool)
    syncs_inserts = [i[1] for i in fake_db.inserts if i[0] == "tally_syncs"]
    assert len(syncs_inserts) == 1
    assert "Ghost Party" in syncs_inserts[0]["error"]
    assert syncs_inserts[0]["success"] is False
    assert syncs_inserts[0]["sync_type"] == "poll"

def test_agent_token_mismatch(fake_db):
    biz_id = str(uuid.uuid4())
    fake_db.storage["businesses"] = [{"id": biz_id, "agent_token": "different_token"}]
    
    payload = {
        "business_id": biz_id,
        "agent_token": "wrong_token",
        "company_name": "TEST",
        "debtors": []
    }
    
    resp = client.post("/tally/import", json=payload)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid agent_token"
