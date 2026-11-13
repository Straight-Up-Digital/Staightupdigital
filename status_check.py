import sqlite3
from pathlib import Path
db = Path("reports/straight_up_digital.sqlite3").resolve()
c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' order by 1")]
print("TABLES:", tables)
for t in tables:
    print(f"  {t}: {c.execute(f'select count(*) from {t}').fetchone()[0]} rows")
cols = [r[1] for r in c.execute("pragma table_info(contacts)")]
print("contacts has tenant_id:", "tenant_id" in cols)
print("contacts encrypted cols:", [x for x in cols if x.endswith("_enc")])
if "tenant_id" in cols:
    print("tenants:", c.execute("select tenant_id, count(*) from contacts group by 1").fetchall())
    print("contacts with marketing_consent=1:", c.execute("select count(*) from contacts where marketing_consent=1").fetchone()[0])
if "audit_jobs" in tables:
    print("audit jobs:", c.execute("select status, count(*) from audit_jobs group by 1").fetchall())
if "api_logs" in tables:
    print("api_logs rows:", c.execute("select count(*) from api_logs").fetchone()[0])
if "suppression_list" in tables:
    print("suppression entries:", c.execute("select count(*) from suppression_list").fetchone()[0])
