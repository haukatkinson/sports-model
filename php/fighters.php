<?php
require_once __DIR__ . '/db.php';

$result = $conn->query('SELECT id, name, weight_class, wins, losses, draws FROM fighters ORDER BY name ASC LIMIT 200');
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Fighters</title>
</head>
<body>
  <h1>Fighters</h1>
  <p><a href="index.php">Back to predictor</a></p>

  <table border="1" cellpadding="6" cellspacing="0">
    <thead>
      <tr>
        <th>Name</th>
        <th>Weight Class</th>
        <th>Record</th>
      </tr>
    </thead>
    <tbody>
      <?php if ($result && $result->num_rows > 0): ?>
        <?php while ($row = $result->fetch_assoc()): ?>
          <tr>
            <td><?= htmlspecialchars($row['name']) ?></td>
            <td><?= htmlspecialchars($row['weight_class'] ?? 'N/A') ?></td>
            <td><?= (int)$row['wins'] ?>-<?= (int)$row['losses'] ?>-<?= (int)$row['draws'] ?></td>
          </tr>
        <?php endwhile; ?>
      <?php else: ?>
        <tr><td colspan="3">No fighters found.</td></tr>
      <?php endif; ?>
    </tbody>
  </table>
</body>
</html>
