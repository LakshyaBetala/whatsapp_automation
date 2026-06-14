import psycopg2

conn_str = "postgresql://postgres.nwwchedfnoxuubavqskh:Laksh%402804%21@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

print("Connecting to Supabase...")
conn = psycopg2.connect(conn_str)
conn.autocommit = True
cursor = conn.cursor()

sql = """
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tally_group text;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS is_opening_balance boolean DEFAULT false;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS agent_token text;
"""

print("Executing migration...")
cursor.execute(sql)
print("Migration completed successfully!")

cursor.close()
conn.close()
