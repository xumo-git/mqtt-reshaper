import os, sqlite3, sys
p = r"C:\mqtt转发\_avtest.db"
for s in ("", "-wal", "-shm"):
    if os.path.exists(p + s):
        os.remove(p + s)

con = sqlite3.connect(p)
print("set ->", con.execute("PRAGMA auto_vacuum = INCREMENTAL").fetchone())
print("read auto_vacuum =", con.execute("PRAGMA auto_vacuum").fetchone())
con.execute("PRAGMA journal_mode=WAL")
con.execute("CREATE TABLE t(x)")
con.commit()

print("after create auto_vacuum =", con.execute("PRAGMA auto_vacuum").fetchone())
for i in range(20000):
    con.execute("INSERT INTO t VALUES (?)", ("y" * 400,))
con.commit()
print("page_count =", con.execute("PRAGMA page_count").fetchone(),
      "freelist =", con.execute("PRAGMA freelist_count").fetchone(),
      "page_size =", con.execute("PRAGMA page_size").fetchone())

con.execute("DELETE FROM t WHERE rowid > 100")
con.commit()
print("after delete freelist =", con.execute("PRAGMA freelist_count").fetchone())
r = con.execute("PRAGMA incremental_vacuum(100000)").fetchone()
print("incremental_vacuum ->", r)
print("page_count =", con.execute("PRAGMA page_count").fetchone(),
      "freelist =", con.execute("PRAGMA freelist_count").fetchone())
con.close()
print("file =", round(os.path.getsize(p) / 1048576, 2), "MB")
for s in ("", "-wal", "-shm"):
    if os.path.exists(p + s):
        os.remove(p + s)
