-- MySQL Schema for Shop System
-- Run this to create shop tables in MySQL

-- Shops table (shop configurations)
CREATE TABLE IF NOT EXISTS shops (
    shop_name VARCHAR(100) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    image_url TEXT,
    footer_text TEXT,
    color VARCHAR(50) DEFAULT 'orange',
    channel_id BIGINT,
    message_id BIGINT,
    thread_id BIGINT,
    created_by BIGINT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_created_by (created_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Shop items table (items belonging to shops)
CREATE TABLE IF NOT EXISTS shop_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    shop_name VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    price INT NOT NULL,
    stock INT DEFAULT -1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (shop_name) REFERENCES shops(shop_name) ON DELETE CASCADE,
    INDEX idx_shop_name (shop_name),
    INDEX idx_price (price)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;