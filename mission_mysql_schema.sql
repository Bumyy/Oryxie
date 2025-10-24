-- MySQL Schema for Mission System
-- Run this to create the mission table in MySQL

CREATE TABLE IF NOT EXISTS scheduled_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    image_url TEXT,
    footer_text TEXT,
    color VARCHAR(50) DEFAULT 'gold',
    author_name TEXT,
    flight_numbers TEXT,
    custom_emojis TEXT,
    multiplier DECIMAL(5,2) DEFAULT 1.0,
    deadline_hours INT DEFAULT 24,
    channel_id BIGINT NOT NULL,
    post_time DATETIME NOT NULL,
    creator_id BIGINT NOT NULL,
    is_posted TINYINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_post_time (post_time),
    INDEX idx_is_posted (is_posted),
    INDEX idx_title (title)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;