-- Run this once to set up the table. On the server:
--   mysql -h localhost -u db-2026s2e -p db-2026s2e < create_donations_table.sql
-- (enter your DB password when prompted)

CREATE TABLE IF NOT EXISTS donations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    food_item VARCHAR(150) NOT NULL,
    quantity_kg DECIMAL(8,2) NOT NULL,
    donor_name VARCHAR(150) NOT NULL,
    status ENUM('Pending', 'Assigned', 'In transit', 'Delivered', 'Cancelled') DEFAULT 'Pending',
    date_listed DATETIME DEFAULT CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4;

-- Sample rows so the page has something to show immediately.
-- Delete these later and replace with real donor submissions.
INSERT INTO donations (food_item, quantity_kg, donor_name, status, date_listed) VALUES
('Bread and pastries', 8.00, 'Corner Bakery', 'Assigned', '2026-09-08 09:15:00'),
('Mixed vegetables', 15.00, 'Fresh Grocer Co.', 'Delivered', '2026-09-07 14:30:00'),
('Sandwiches and wraps', 12.50, 'CityDeli', 'Delivered', '2026-09-06 11:00:00'),
('Canned goods', 20.00, 'Community Pantry Donation Drive', 'Pending', '2026-09-09 08:00:00');
