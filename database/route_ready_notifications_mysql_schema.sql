-- Apply with a database account that has CREATE TABLE privileges.
CREATE TABLE IF NOT EXISTS if_route_ready_schedule_state (
    schedule_id VARCHAR(64) NOT NULL PRIMARY KEY,
    last_status INT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS if_route_ready_dm_events (
    event_key CHAR(64) NOT NULL PRIMARY KEY,
    arrival_schedule_id VARCHAR(64) NOT NULL,
    next_schedule_id VARCHAR(64) NOT NULL,
    if_user_id VARCHAR(64) NOT NULL,
    discord_user_id VARCHAR(32) NOT NULL,
    delivery_status VARCHAR(16) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_if_route_ready_arrival (arrival_schedule_id),
    INDEX idx_if_route_ready_next (next_schedule_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
