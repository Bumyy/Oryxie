-- Apply with a database account that has CREATE TABLE privileges.
CREATE TABLE IF NOT EXISTS if_stopped_flight_incidents (
    schedule_id VARCHAR(64) NOT NULL PRIMARY KEY,
    aircraft_id VARCHAR(64) NOT NULL,
    registration VARCHAR(32) NOT NULL,
    callsign VARCHAR(64) NOT NULL,
    origin_icao VARCHAR(8) NOT NULL,
    destination_icao VARCHAR(8) NOT NULL,
    original_crew_json TEXT NOT NULL,
    stopped_since DATETIME NULL,
    lifecycle VARCHAR(16) NOT NULL DEFAULT 'monitoring',
    eta_warning_sent TINYINT(1) NOT NULL DEFAULT 0,
    one_hour_sent TINYINT(1) NOT NULL DEFAULT 0,
    two_hour_sent TINYINT(1) NOT NULL DEFAULT 0,
    three_hour_sent TINYINT(1) NOT NULL DEFAULT 0,
    four_hour_sent TINYINT(1) NOT NULL DEFAULT 0,
    claim_user_id VARCHAR(64) NULL,
    alert_channel_id VARCHAR(32) NULL,
    alert_message_id VARCHAR(32) NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_stopped_flight_lifecycle (lifecycle, stopped_since)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS if_stopped_flight_dm_events (
    schedule_id VARCHAR(64) NOT NULL,
    if_user_id VARCHAR(64) NOT NULL,
    discord_user_id VARCHAR(32) NOT NULL,
    wave TINYINT UNSIGNED NOT NULL,
    flight_count_7d INT UNSIGNED NOT NULL DEFAULT 0,
    channel_id VARCHAR(32) NULL,
    message_id VARCHAR(32) NULL,
    delivery_status VARCHAR(16) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (schedule_id, if_user_id),
    INDEX idx_stopped_flight_dm_wave (schedule_id, wave)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
