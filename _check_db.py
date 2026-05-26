from core.database import get_conn

conn = get_conn()
cur = conn.cursor()

# Check counts
cur.execute('SELECT COUNT(*) as total FROM jobs')
total = cur.fetchone()['total']

cur.execute('SELECT COUNT(*) as with_posted FROM jobs WHERE posted_at IS NOT NULL')
with_posted = cur.fetchone()['with_posted']

print(f'Total jobs: {total}')
print(f'Jobs with posted_at: {with_posted}')

if with_posted > 0:
    cur.execute('SELECT title, posted_at FROM jobs WHERE posted_at IS NOT NULL LIMIT 3')
    for row in cur.fetchall():
        print(f'  - {row["title"]}: {row["posted_at"]}')
else:
    print('No jobs have posted_at data yet')
    
conn.close()
