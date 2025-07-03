
            CREATE TABLE memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                query TEXT NOT NULL,
                sources TEXT,
                answer TEXT NOT NULL
            );
            