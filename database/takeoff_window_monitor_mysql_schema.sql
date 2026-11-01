-- Apply this file in phpMyAdmin or your MySQL client if the bot's
-- database account does not have CREATE TABLE permissions.

CREATE TABLE IF NOT EXISTS if_ground_takeoff_monitor_state (
    schedule_id VARCHAR(64) NOT NULL PRIMARY KEY,
    aircraft_id VARCHAR(64) NOT NULL,
    registration VARCHAR(32) NOT NULL,
    callsign VARCHAR(64) NOT NULL,
    origin_icao VARCHAR(8) NOT NULL,
    destination_icao VARCHAR(8) NOT NULL,
    parked_icao VARCHAR(8) NOT NULL,
    crew_json TEXT NOT NULL,
    assigned_since DATETIME NOT NULL,
    takeoff_limit_hours INT NOT NULL,
    warning_sent TINYINT(1) NOT NULL DEFAULT 0,
    warning_sent_at DATETIME NULL,
    unassigned TINYINT(1) NOT NULL DEFAULT 0,
    unassigned_at DATETIME NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_takeoff_aircraft (aircraft_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
