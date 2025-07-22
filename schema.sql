-- schema.sql

-- Core Memory: Persistent agent and user information
CREATE TABLE IF NOT EXISTS core_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    segment TEXT NOT NULL, -- 'persona' or 'human'
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, segment, key)
);

-- Episodic Memory: Time-stamped events and user interactions
CREATE TABLE IF NOT EXISTS episodic_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL, -- 'user', 'assistant', 'system'
    content TEXT NOT NULL,
    final_data_json TEXT, -- Full JSON packet for assistant turns
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Semantic Memory: Abstract concepts, knowledge graphs, and named entities
CREATE TABLE IF NOT EXISTS semantic_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL, -- 'concept', 'person', 'place', 'organization'
    summary TEXT NOT NULL,
    details TEXT,
    source TEXT, -- e.g., chat_id or URL
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, entity_type, summary)
);

-- Resource Memory: References to external documents, images, and other media
CREATE TABLE IF NOT EXISTS resource_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER,
    title TEXT NOT NULL,
    summary TEXT,
    resource_type TEXT NOT NULL, -- 'image', 'video', 'url', 'file'
    link_or_content TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Procedural Memory: Workflows and task sequences (future-proofing)
CREATE TABLE IF NOT EXISTS procedural_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    task_name TEXT NOT NULL UNIQUE,
    description TEXT,
    steps_json TEXT NOT NULL, -- JSON array of steps
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Knowledge Vault: Verbatim facts and sensitive information (future-proofing)
CREATE TABLE IF NOT EXISTS knowledge_vault (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL UNIQUE,
    value_encrypted TEXT NOT NULL,
    sensitivity_label TEXT NOT NULL, -- 'low', 'medium', 'high'
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Original users and chats tables
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

-- Remove the old memory table if it exists
DROP TABLE IF EXISTS memory;
