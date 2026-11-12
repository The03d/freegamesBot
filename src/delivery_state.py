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
        '''Closes the connection to the delivery state database.'''
        self.connection.close()

    def remember_profile(self, name, state_id):
        '''Remembers a removed profile by storing its state ID.

        Args:
            name (str): The name of the removed profile.
            state_id (str): The state ID associated with the profile.
        '''
        with self.connection:
            self.connection.execute('''
                INSERT INTO removed_profiles(name, state_id) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET state_id=excluded.state_id
            ''', (name, state_id))

    def removed_profile_id(self, name):
        '''Retrieves the state ID of a removed profile.

        Args:
            name (str): The name of the removed profile.

        Returns:
            str or None: The state ID if the profile was removed, None otherwise.
        '''
        row = self.connection.execute('SELECT state_id FROM removed_profiles WHERE name=?', (name,)).fetchone()
        return row['state_id'] if row else None

    def enqueue(self, profile, item):
        '''Adds a deal item to the queue for the specified profile.

        Args:
            profile (str): The profile name.
            item (dict): The deal item to enqueue.
        '''
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
        '''Retrieves all queued deal items for the specified profile.

        Args:
            profile (str): The profile name.

        Returns:
            list of tuple: A list of tuples containing the entry ID and the deal item.
        '''
        rows = self.connection.execute(
            "SELECT id, payload FROM queue WHERE profile=? AND status='queued' ORDER BY id", (profile,)
        )
        return [(row['id'], json.loads(row['payload'])) for row in rows]

    def blocked(self, profile, game_id, shop_id):
        '''Checks if a deal item is currently blocked (inflight) for the specified profile.

        Args:
            profile (str): The profile name.
            game_id (str): The ID of the game.
            shop_id (str): The ID of the shop.

        Returns:
            bool: True if the deal item is inflight, False otherwise.
        '''
        return self.connection.execute('''
            SELECT 1 FROM queue WHERE profile=? AND game_id=? AND shop_id=? AND status='inflight'
        ''', (profile, game_id, shop_id)).fetchone() is not None

    def remove(self, entry_id):
        '''Marks a queued deal item as discarded.

        Args:
            entry_id (int): The ID of the queue entry to remove.
        '''
        with self.connection:
            self.connection.execute("UPDATE queue SET status='discarded' WHERE id=? AND status='queued'", (entry_id,))

    def update_payload(self, entry_id, item):
        '''Updates the payload of a queued deal item.

        Args:
            entry_id (int): The ID of the queue entry to update.
            item (dict): The new deal item payload.
        '''
        with self.connection:
            self.connection.execute(
                "UPDATE queue SET payload=? WHERE id=? AND status='queued'", (json.dumps(item), entry_id)
            )

    def counts(self, profile, day):
        '''Retrieves the counts of used and sent deliveries for the specified profile and day.

        Args:
            profile (str): The profile name.
            day (str): The day to check.

        Returns:
            tuple: A tuple containing the number of used deliveries and the number of sent deliveries.
        '''
        row = self.connection.execute('''
            SELECT COUNT(*) AS used,
                   COALESCE(SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END), 0) AS sent
            FROM deliveries WHERE profile=? AND day=? AND status IN ('reserved', 'sent')
        ''', (profile, day)).fetchone()
        return row['used'], row['sent']

    def reserve(self, profile, entry_id, day, maximum):
        '''Reserves a queued deal item for the specified profile and day, if the maximum number of used deliveries has not been reached.

        Args:
            profile (str): The profile name.
            entry_id (int): The ID of the queue entry to reserve.
            day (str): The day for which to reserve the delivery.
            maximum (int): The maximum number of used deliveries allowed for the day.

        Returns:
            int or None: The ID of the newly created delivery record if successful, None otherwise.
        '''
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
        '''Confirms a reserved delivery, marking it as sent and updating the associated queue entry.

        Args:
            delivery_id (int): The ID of the delivery to confirm.
            record (dict): The record to store with the confirmed delivery.

        Raises:
            ValueError: If the delivery is not awaiting confirmation.
        '''
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
        '''Rejects a reserved delivery, marking it as rejected and returning the associated queue entry to the queue.

        Args:
            delivery_id (int): The ID of the delivery to reject.

        Raises:
            ValueError: If the delivery is not awaiting confirmation.
        '''
        with self.connection:
            row = self.connection.execute(
                "SELECT entry_id FROM deliveries WHERE id=? AND status='reserved'", (delivery_id,)
            ).fetchone()
            if row is None:
                raise ValueError('Delivery is not awaiting confirmation.')
            self.connection.execute("UPDATE deliveries SET status='rejected' WHERE id=?", (delivery_id,))
            self.connection.execute("UPDATE queue SET status='queued' WHERE id=?", (row['entry_id'],))

    def uncertain(self, profile):
        '''Retrieves all uncertain (reserved) deliveries for the specified profile.

        Args:
            profile (str): The profile name.

        Returns:
            list of tuple: A list of tuples containing the delivery ID, day, and deal item payload.
        '''
        rows = self.connection.execute('''
            SELECT deliveries.id, deliveries.day, queue.payload FROM deliveries
            JOIN queue ON queue.id=deliveries.entry_id
            WHERE deliveries.profile=? AND deliveries.status='reserved' ORDER BY deliveries.id
        ''', (profile,))
        return [(row['id'], row['day'], json.loads(row['payload'])) for row in rows]

    def sent_records(self, profile):
        '''Retrieves all sent delivery records for the specified profile.

        Args:
            profile (str): The profile name.

        Returns:
            list of dict: A list of JSON-decoded delivery records.
        '''
        rows = self.connection.execute(
            "SELECT record FROM deliveries WHERE profile=? AND status='sent' ORDER BY id", (profile,)
        )
        return [json.loads(row['record']) for row in rows]