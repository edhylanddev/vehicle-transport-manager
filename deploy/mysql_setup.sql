-- Run as MySQL root:  sudo mysql < deploy/mysql_setup.sql
-- Pick a NEW strong password and put the same one in .env DATABASE_URL.
CREATE DATABASE IF NOT EXISTS vtm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'vtm'@'localhost' IDENTIFIED BY 'CHANGE_ME_TO_A_LONG_RANDOM_PASSWORD';
GRANT ALL PRIVILEGES ON vtm.* TO 'vtm'@'localhost';
FLUSH PRIVILEGES;
