-- AI personality configuration storage
CREATE TABLE IF NOT EXISTS personalities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    config_json TEXT NOT NULL,
    source TEXT DEFAULT 'ai_generated',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_personalities_name ON personalities(name);
CREATE INDEX IF NOT EXISTS idx_personalities_source ON personalities(source);

-- Avatar images for personalities
CREATE TABLE IF NOT EXISTS avatar_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    personality_name TEXT NOT NULL,
    emotion TEXT NOT NULL,
    image_data BLOB NOT NULL,
    thumbnail_data BLOB,
    full_image_data BLOB,
    generation_prompt TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(personality_name, emotion)
);

CREATE INDEX IF NOT EXISTS idx_avatar_images_personality ON avatar_images(personality_name);
CREATE INDEX IF NOT EXISTS idx_avatar_images_emotion ON avatar_images(personality_name, emotion);
