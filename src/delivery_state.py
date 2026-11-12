import json
import sqlite3


class DeliveryState:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        with self.connection:
            self.connection.executescript('''
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY,
                    profile TEXT NOT NULL,
                    game_id TEXT NOT NULL,
                    shop_id INTEGER NOT NULL,
                    price_cut REAL NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    UNIQUE(profile, game_id, shop_id, price_cut)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY,
                    entry_id INTEGER NOT NULL,
                    profile TEXT NOT NULL,
                    day TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record TEXT,
                    FOREIGN KEY(entry_id) REFERENCES queue(id)
                );
                CREATE TABLE IF NOT EXISTS removed_profiles (
                    name TEXT PRIMARY KEY,
                    state_id TEXT NOT NULL
                );
            ''')

    def close(self):
        self.connection.close()

    def remember_profile(self, name, state_id):
        with self.connection:
            self.connection.execute('''
                INSERT INTO removed_profiles(name, state_id) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET state_id=excluded.state_id
            ''', (name, state_id))

    def removed_profile_id(self, name):
        row = self.connection.execute('SELECT state_id FROM removed_profiles WHERE name=?', (name,)).fetchone()
        return row['state_id'] if row else None

    def enqueue(self, profile, item):
        with self.connection:
            uncertain = self.connection.execute('''
                SELECT 1 FROM queue WHERE profile=? AND game_id=? AND shop_id=? AND status='inflight'
            ''', (profile, item['id'], item['deal']['shop']['id'])).fetchone()
            if uncertain:
                return
            self.connection.execute('''
                INSERT INTO queue(profile, game_id, shop_id, price_cut, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile, game_id, shop_id, price_cut) DO UPDATE SET
                    payload=excluded.payload, status='queued' WHERE queue.status IN ('queued', 'discarded')
            ''', (profile, item['id'], item['deal']['shop']['id'], item['deal']['cut'], json.dumps(item)))

    def queued(self, profile):
        rows = self.connection.execute(
            "SELECT id, payload FROM queue WHERE profile=? AND status='queued' ORDER BY id", (profile,)
        )
        return [(row['id'], json.loads(row['payload'])) for row in rows]

    def blocked(self, profile, game_id, shop_id):
        return self.connection.execute('''
            SELECT 1 FROM queue WHERE profile=? AND game_id=? AND shop_id=? AND status='inflight'
        ''', (profile, game_id, shop_id)).fetchone() is not None

    def remove(self, entry_id):
        with self.connection:
            self.connection.execute("UPDATE queue SET status='discarded' WHERE id=? AND status='queued'", (entry_id,))

    def update_payload(self, entry_id, item):
        with self.connection:
            self.connection.execute(
                "UPDATE queue SET payload=? WHERE id=? AND status='queued'", (json.dumps(item), entry_id)
            )

    def counts(self, profile, day):
        row = self.connection.execute('''
            SELECT COUNT(*) AS used,
                   COALESCE(SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END), 0) AS sent
            FROM deliveries WHERE profile=? AND day=? AND status IN ('reserved', 'sent')
        ''', (profile, day)).fetchone()
        return row['used'], row['sent']

    def reserve(self, profile, entry_id, day, maximum):
        with self.connection:
            used, _ = self.counts(profile, day)
            if used >= maximum:
                return None
            updated = self.connection.execute(
                "UPDATE queue SET status='inflight' WHERE id=? AND profile=? AND status='queued'",
                (entry_id, profile),
            )
            if updated.rowcount != 1:
                return None
            cursor = self.connection.execute(
                "INSERT INTO deliveries(entry_id, profile, day, status) VALUES (?, ?, ?, 'reserved')",
                (entry_id, profile, day),
            )
            return cursor.lastrowid

    def confirm(self, delivery_id, record):
        with self.connection:
            row = self.connection.execute(
                "SELECT entry_id FROM deliveries WHERE id=? AND status='reserved'", (delivery_id,)
            ).fetchone()
            if row is None:
                raise ValueError('Delivery is not awaiting confirmation.')
            self.connection.execute(
                "UPDATE deliveries SET status='sent', record=? WHERE id=?", (json.dumps(record), delivery_id)
            )
            self.connection.execute("UPDATE queue SET status='sent' WHERE id=?", (row['entry_id'],))

    def reject(self, delivery_id):
        with self.connection:
            row = self.connection.execute(
                "SELECT entry_id FROM deliveries WHERE id=? AND status='reserved'", (delivery_id,)
            ).fetchone()
            if row is None:
                raise ValueError('Delivery is not awaiting confirmation.')
            self.connection.execute("UPDATE deliveries SET status='rejected' WHERE id=?", (delivery_id,))
            self.connection.execute("UPDATE queue SET status='queued' WHERE id=?", (row['entry_id'],))

    def uncertain(self, profile):
        rows = self.connection.execute('''
            SELECT deliveries.id, deliveries.day, queue.payload FROM deliveries
            JOIN queue ON queue.id=deliveries.entry_id
            WHERE deliveries.profile=? AND deliveries.status='reserved' ORDER BY deliveries.id
        ''', (profile,))
        return [(row['id'], row['day'], json.loads(row['payload'])) for row in rows]

    def sent_records(self, profile):
        rows = self.connection.execute(
            "SELECT record FROM deliveries WHERE profile=? AND status='sent' ORDER BY id", (profile,)
        )
        return [json.loads(row['record']) for row in rows]