-- Run once against the bot database if if_stopped_flight_incidents predates
-- these ETA warning retry columns.
ALTER TABLE if_stopped_flight_incidents
    ADD COLUMN eta_warning_attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN eta_warning_last_attempt_at DATETIME NULL;
