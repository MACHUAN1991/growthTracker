import sqlite3
conn = sqlite3.connect('photos.db')
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM photos WHERE file_type='photo'")
print('DB photos:', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM photos")
print('DB total:', cur.fetchone()[0])
conn.close()