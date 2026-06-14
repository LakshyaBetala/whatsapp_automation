-- Add tally grouping and opening balance flags
ALTER TABLE clients ADD COLUMN tally_group text;
ALTER TABLE bills ADD COLUMN is_opening_balance boolean DEFAULT false;

-- Add agent_token for the Tally integration authentication
ALTER TABLE businesses ADD COLUMN agent_token text;
