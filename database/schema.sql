CREATE DATABASE IF NOT EXISTS ufc_db;
USE ufc_db;

CREATE TABLE IF NOT EXISTS weight_classes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    min_weight INT DEFAULT NULL,
    max_weight INT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    event_date DATE NOT NULL,
    location VARCHAR(150) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_event_name_date (name, event_date),
    INDEX idx_events_date (event_date)
);

CREATE TABLE IF NOT EXISTS fighters (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(120) NOT NULL UNIQUE,
    nickname VARCHAR(120) DEFAULT NULL,
    weight_class VARCHAR(60) DEFAULT NULL,
    weight_class_id INT DEFAULT NULL,
    stance VARCHAR(60) DEFAULT NULL,
    reach_cm DECIMAL(5,2) DEFAULT NULL,
    date_of_birth DATE DEFAULT NULL,
    wins INT DEFAULT 0,
    losses INT DEFAULT 0,
    draws INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (weight_class_id) REFERENCES weight_classes(id),
    INDEX idx_fighter_weight_class_id (weight_class_id)
);

CREATE TABLE IF NOT EXISTS fights (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_id INT DEFAULT NULL,
    event_name VARCHAR(150) NOT NULL,
    event_date DATE NOT NULL,
    weight_class_id INT DEFAULT NULL,
    fighter_a_id INT NOT NULL,
    fighter_b_id INT NOT NULL,
    winner_id INT DEFAULT NULL,
    method VARCHAR(120) DEFAULT NULL,
    round_num INT DEFAULT NULL,
    time_in_round VARCHAR(20) DEFAULT NULL,
    is_title_fight TINYINT(1) DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id),
    FOREIGN KEY (weight_class_id) REFERENCES weight_classes(id),
    FOREIGN KEY (fighter_a_id) REFERENCES fighters(id),
    FOREIGN KEY (fighter_b_id) REFERENCES fighters(id),
    FOREIGN KEY (winner_id) REFERENCES fighters(id),
    INDEX idx_event_date (event_date),
    INDEX idx_event_id (event_id),
    INDEX idx_fight_weight_class_id (weight_class_id),
    INDEX idx_fighter_a (fighter_a_id),
    INDEX idx_fighter_b (fighter_b_id)
);

CREATE TABLE IF NOT EXISTS fighter_stats (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    fighter_id INT NOT NULL,
    fight_id BIGINT NOT NULL,
    sig_strikes_landed INT DEFAULT 0,
    sig_strikes_attempted INT DEFAULT 0,
    takedowns_landed INT DEFAULT 0,
    takedowns_attempted INT DEFAULT 0,
    submission_attempts INT DEFAULT 0,
    knockdowns INT DEFAULT 0,
    control_time_seconds INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (fighter_id) REFERENCES fighters(id),
    FOREIGN KEY (fight_id) REFERENCES fights(id),
    UNIQUE KEY uniq_fighter_fight (fighter_id, fight_id)
);

CREATE TABLE IF NOT EXISTS fighter_fight_metrics (
    fighter_id INT NOT NULL,
    fight_id BIGINT NOT NULL,
    strikes_per_min DECIMAL(7,4) DEFAULT 0,
    strike_accuracy DECIMAL(7,4) DEFAULT 0,
    takedown_defense DECIMAL(7,4) DEFAULT 0,
    takedown_accuracy DECIMAL(7,4) DEFAULT 0,
    win_streak INT DEFAULT 0,
    avg_fight_time_seconds INT DEFAULT 0,
    last_5_fights_wins INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fighter_id, fight_id),
    FOREIGN KEY (fighter_id) REFERENCES fighters(id),
    FOREIGN KEY (fight_id) REFERENCES fights(id)
);

CREATE TABLE IF NOT EXISTS fighter_style (
    fighter_id INT PRIMARY KEY,
    striking_rating DECIMAL(5,2) DEFAULT 0,
    grappling_rating DECIMAL(5,2) DEFAULT 0,
    cardio_rating DECIMAL(5,2) DEFAULT 0,
    pressure_rating DECIMAL(5,2) DEFAULT 0,
    durability_rating DECIMAL(5,2) DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (fighter_id) REFERENCES fighters(id)
);

CREATE TABLE IF NOT EXISTS fight_odds (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    fight_id BIGINT NOT NULL,
    fighter_a_odds DECIMAL(8,2) DEFAULT NULL,
    fighter_b_odds DECIMAL(8,2) DEFAULT NULL,
    closing_odds_time DATETIME DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (fight_id) REFERENCES fights(id),
    UNIQUE KEY uniq_fight_odds_snapshot (fight_id, closing_odds_time)
);

INSERT IGNORE INTO weight_classes (id, name, min_weight, max_weight) VALUES
    (1, 'Strawweight', 106, 115),
    (2, 'Flyweight', 116, 125),
    (3, 'Bantamweight', 126, 135),
    (4, 'Featherweight', 136, 145),
    (5, 'Lightweight', 146, 155),
    (6, 'Welterweight', 156, 170),
    (7, 'Middleweight', 171, 185),
    (8, 'Light Heavyweight', 186, 205),
    (9, 'Heavyweight', 206, 265),
    (10, 'Women's Strawweight', 106, 115),
    (11, 'Women's Flyweight', 116, 125),
    (12, 'Women's Bantamweight', 126, 135),
    (13, 'Women's Featherweight', 136, 145),
    (14, 'Catchweight', NULL, NULL),
    (15, 'Openweight', NULL, NULL);
