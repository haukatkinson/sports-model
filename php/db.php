<?php
$host = getenv('DB_HOST') ?: 'localhost';
$user = getenv('DB_USER') ?: 'user';
$pass = getenv('DB_PASS') ?: 'pass';
$name = getenv('DB_NAME') ?: 'ufc_db';

$conn = new mysqli($host, $user, $pass, $name);

if ($conn->connect_error) {
    http_response_code(500);
    die('Database connection failed: ' . $conn->connect_error);
}
