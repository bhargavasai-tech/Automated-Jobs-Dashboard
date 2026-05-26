from core.database import get_conn

conn = get_conn()
cur = conn.cursor()

# Truncate tables to delete all data and reset sequences
cur.execute("TRUNCATE TABLE jobs RESTART IDENTITY CASCADE;")
cur.execute("TRUNCATE TABLE runs RESTART IDENTITY CASCADE;")
conn.commit()

print("[OK] Successfully cleared all jobs and runs from the database!")
cur.close()
conn.close()
