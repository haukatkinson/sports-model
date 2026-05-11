<?php
function getPredictionApiUrl(): string
{
  $configured = getenv('PREDICT_API_URL');
  if (is_string($configured) && trim($configured) !== '') {
    return rtrim(trim($configured), '/');
  }

  return 'http://127.0.0.1:5000/predict';
}

function tryDbConnection(): ?mysqli
{
  mysqli_report(MYSQLI_REPORT_OFF);

  $host = getenv('DB_HOST') ?: 'localhost';
  $user = getenv('DB_USER') ?: 'user';
  $pass = getenv('DB_PASS') ?: 'pass';
  $name = getenv('DB_NAME') ?: 'ufc_db';

  $conn = @new mysqli($host, $user, $pass, $name);
  if ($conn->connect_error) {
    return null;
  }

  return $conn;
}

function callPredictionApi(string $fighterA, string $fighterB): array
{
    $payload = json_encode([
        'fighterA' => $fighterA,
        'fighterB' => $fighterB,
    ]);

    $context = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: application/json\r\n",
            'content' => $payload,
            'timeout' => 5,
        ]
    ]);

    $response = @file_get_contents(getPredictionApiUrl(), false, $context);
    if ($response === false) {
        return [
            'error' => 'Prediction service unavailable',
        ];
    }

    $decoded = json_decode($response, true);
    if (!is_array($decoded)) {
        return [
            'error' => 'Invalid prediction response',
        ];
    }

    return $decoded;
}

function americanOddsToImpliedProbability($odds): ?float
{
  if ($odds === null || $odds === '') {
    return null;
  }

  $value = (int)$odds;
  if ($value === 0) {
    return null;
  }

  if ($value > 0) {
    return 100 / ($value + 100);
  }

  $absValue = abs($value);
  return $absValue / ($absValue + 100);
}

function formatAmericanOdds($odds): string
{
  if ($odds === null || $odds === '') {
    return 'N/A';
  }

  $value = (int)$odds;
  return $value > 0 ? '+' . $value : (string)$value;
}

function getBetTier(float $edgePercent, int $odds): array
{
  $isUnderdog = $odds > 0;

  if ($isUnderdog) {
    if ($edgePercent >= 8.0) {
      return ['label' => 'T1 Live Dog', 'desc' => 'Real underdog value. Worth a sprinkle.', 'class' => 'tier-live'];
    }
    if ($edgePercent >= 3.0) {
      return ['label' => 'T2 Puncher\'s', 'desc' => 'Sprinkle only if you love the price.', 'class' => 'tier-punch'];
    }
    return ['label' => 'T3 Dead Dog', 'desc' => 'Hard pass in all formats.', 'class' => 'tier-dead'];
  }

  if ($edgePercent >= 12.0) {
    return ['label' => 'T1 Elite', 'desc' => 'Lock it in.', 'class' => 'tier-elite'];
  }
  if ($edgePercent >= 7.0) {
    return ['label' => 'T2 Strong', 'desc' => 'High confidence. Solid play.', 'class' => 'tier-strong'];
  }
  if ($edgePercent >= 3.0) {
    return ['label' => 'T3 Volatile', 'desc' => 'Playable but proceed with caution.', 'class' => 'tier-volatile'];
  }
  if ($edgePercent >= 0.0) {
    return ['label' => 'T4 Fragile', 'desc' => 'Avoid unless desperate.', 'class' => 'tier-fragile'];
  }

  return ['label' => 'T5 Trap', 'desc' => 'Stay away. Model says no.', 'class' => 'tier-trap'];
}

function loadNearestEventFights(string $csvPath): array
{
    if (!file_exists($csvPath)) {
        return [];
    }

    $handle = fopen($csvPath, 'r');
    if ($handle === false) {
        return [];
    }

    $rows = [];
    $headers = fgetcsv($handle);
    if ($headers === false) {
        fclose($handle);
        return [];
    }

    while (($data = fgetcsv($handle)) !== false) {
        if (count($data) !== count($headers)) {
            continue;
        }
        $rows[] = array_combine($headers, $data);
    }

    fclose($handle);
    return $rows;
}

  function buildFightKey(array $fight): string
  {
    return md5(($fight['fighterA'] ?? '') . '|' . ($fight['fighterB'] ?? ''));
  }

  function findFightId(mysqli $conn, array $fight): ?int
  {
    $sql = "
      SELECT f.id
      FROM fights f
      INNER JOIN fighters fa ON fa.id = f.fighter_a_id
      INNER JOIN fighters fb ON fb.id = f.fighter_b_id
      WHERE f.event_name = ?
        AND f.event_date = ?
        AND fa.name = ?
        AND fb.name = ?
      LIMIT 1
    ";

    $stmt = $conn->prepare($sql);
    if (!$stmt) {
      return null;
    }

    $stmt->bind_param('ssss', $fight['event_name'], $fight['event_date'], $fight['fighterA'], $fight['fighterB']);
    $stmt->execute();
    $result = $stmt->get_result();
    $row = $result ? $result->fetch_assoc() : null;
    $stmt->close();

    return $row ? (int)$row['id'] : null;
  }

  function loadStoredOddsForFight(mysqli $conn, int $fightId): ?array
  {
    $stmt = $conn->prepare(
      "SELECT fighter_a_odds, fighter_b_odds FROM fight_odds WHERE fight_id = ? ORDER BY created_at DESC, id DESC LIMIT 1"
    );
    if (!$stmt) {
      return null;
    }

    $stmt->bind_param('i', $fightId);
    $stmt->execute();
    $result = $stmt->get_result();
    $row = $result ? $result->fetch_assoc() : null;
    $stmt->close();

    return $row ?: null;
  }

  function saveStoredOddsForFight(mysqli $conn, int $fightId, $fighterAOdds, $fighterBOdds): bool
  {
    $selectStmt = $conn->prepare("SELECT id FROM fight_odds WHERE fight_id = ? ORDER BY created_at DESC, id DESC LIMIT 1");
    if (!$selectStmt) {
      return false;
    }

    $selectStmt->bind_param('i', $fightId);
    $selectStmt->execute();
    $result = $selectStmt->get_result();
    $existing = $result ? $result->fetch_assoc() : null;
    $selectStmt->close();

    $a = ($fighterAOdds === '' || $fighterAOdds === null) ? null : (string)$fighterAOdds;
    $b = ($fighterBOdds === '' || $fighterBOdds === null) ? null : (string)$fighterBOdds;

    if ($existing) {
      $updateStmt = $conn->prepare(
        "UPDATE fight_odds SET fighter_a_odds = ?, fighter_b_odds = ?, closing_odds_time = NOW() WHERE id = ?"
      );
      if (!$updateStmt) {
        return false;
      }

      $id = (int)$existing['id'];
      $updateStmt->bind_param('ssi', $a, $b, $id);
      $ok = $updateStmt->execute();
      $updateStmt->close();
      return $ok;
    }

    $insertStmt = $conn->prepare(
      "INSERT INTO fight_odds (fight_id, fighter_a_odds, fighter_b_odds, closing_odds_time) VALUES (?, ?, ?, NOW())"
    );
    if (!$insertStmt) {
      return false;
    }

    $insertStmt->bind_param('iss', $fightId, $a, $b);
    $ok = $insertStmt->execute();
    $insertStmt->close();
    return $ok;
  }

$csvPath = dirname(__DIR__) . '/data/nearest_event_fights.csv';
$fights = loadNearestEventFights($csvPath);
$eventName = $fights[0]['event_name'] ?? 'Latest UFC Card';
$eventDate = $fights[0]['event_date'] ?? null;
$submittedOdds = $_POST['odds'] ?? [];
  $dbStatusMessage = null;
  $dbConn = tryDbConnection();

foreach ($fights as &$fight) {
    $fight['fight_id'] = null;
    $fightKey = buildFightKey($fight);

    if ($dbConn instanceof mysqli) {
      $fight['fight_id'] = findFightId($dbConn, $fight);
      if ($fight['fight_id']) {
        if (isset($submittedOdds[$fightKey])) {
          $saved = saveStoredOddsForFight(
            $dbConn,
            $fight['fight_id'],
            $submittedOdds[$fightKey]['fighterA'] ?? '',
            $submittedOdds[$fightKey]['fighterB'] ?? ''
          );
          $dbStatusMessage = $saved
            ? 'Odds were saved and will auto-fill next time.'
            : 'Could not save some odds to the database.';
        }

        $storedOdds = loadStoredOddsForFight($dbConn, $fight['fight_id']);
        if ($storedOdds) {
          $fight['fighterA_saved_odds'] = $storedOdds['fighter_a_odds'];
          $fight['fighterB_saved_odds'] = $storedOdds['fighter_b_odds'];
        }
      }
    }

    $prediction = callPredictionApi($fight['fighterA'], $fight['fighterB']);
    $fight['prediction'] = $prediction;

    $fighterAOdds = $submittedOdds[$fightKey]['fighterA'] ?? ($fight['fighterA_saved_odds'] ?? '');
    $fighterBOdds = $submittedOdds[$fightKey]['fighterB'] ?? ($fight['fighterB_saved_odds'] ?? '');
    $fight['fighterA_odds'] = $fighterAOdds;
    $fight['fighterB_odds'] = $fighterBOdds;

    if (!isset($prediction['error'])) {
        $probA = (float)($prediction['probabilities'][$fight['fighterA']] ?? 0);
        $probB = (float)($prediction['probabilities'][$fight['fighterB']] ?? 0);
        $fight['fighterA_probability'] = $probA;
        $fight['fighterB_probability'] = $probB;
        $fight['confidence'] = max($probA, $probB) * 100;

      $impliedA = americanOddsToImpliedProbability($fighterAOdds);
      $impliedB = americanOddsToImpliedProbability($fighterBOdds);
      $fight['fighterA_implied'] = $impliedA;
      $fight['fighterB_implied'] = $impliedB;

      if ($impliedA !== null && $impliedB !== null) {
        $edgeA = ($probA - $impliedA) * 100;
        $edgeB = ($probB - $impliedB) * 100;
        $recommendA = $edgeA >= $edgeB;

        $fight['recommended_side'] = $recommendA ? $fight['fighterA'] : $fight['fighterB'];
        $fight['recommended_odds'] = (int)($recommendA ? $fighterAOdds : $fighterBOdds);
        $fight['recommended_model_probability'] = $recommendA ? $probA : $probB;
        $fight['recommended_implied_probability'] = $recommendA ? $impliedA : $impliedB;
        $fight['recommended_edge'] = $recommendA ? $edgeA : $edgeB;
        $fight['tier'] = getBetTier($fight['recommended_edge'], $fight['recommended_odds']);
      }
    }
}
unset($fight);

  if (!($dbConn instanceof mysqli)) {
    $dbStatusMessage = 'Database connection unavailable. Odds will work for this page load only.';
  } else {
    $dbConn->close();
  }
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>UFC Fight Predictions</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b1020;
      --panel: #121a2f;
      --panel-alt: #1a2542;
      --text: #edf2ff;
      --muted: #9fb0d4;
      --accent: #7c3aed;
      --accent-2: #22c55e;
      --danger: #ef4444;
      --border: rgba(255,255,255,0.08);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: radial-gradient(circle at top, #182445 0%, var(--bg) 45%);
      color: var(--text);
    }

    .container {
      width: min(1150px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 64px;
    }

    .hero {
      background: linear-gradient(135deg, rgba(124,58,237,0.18), rgba(34,197,94,0.12));
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 28px;
      margin-bottom: 24px;
      backdrop-filter: blur(10px);
    }

    .eyebrow {
      color: #c4b5fd;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      font-size: 12px;
      margin-bottom: 10px;
    }

    h1, h2, h3, p { margin-top: 0; }

    .hero-grid {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 24px;
      align-items: start;
    }

    .quick-form,
    .fight-card {
      background: rgba(10, 15, 30, 0.65);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 20px;
    }

    .quick-form form {
      display: grid;
      gap: 12px;
    }

    input {
      width: 100%;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: #0d152b;
      color: var(--text);
      padding: 12px 14px;
    }

    button,
    .link-btn {
      display: inline-block;
      border: none;
      border-radius: 12px;
      padding: 12px 16px;
      background: linear-gradient(135deg, var(--accent), #2563eb);
      color: white;
      text-decoration: none;
      font-weight: 700;
      cursor: pointer;
    }

    .sub-links {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 14px;
    }

    .sub-links a:last-child {
      background: #16203c;
    }

    .section-title {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin: 24px 0 14px;
    }

    .section-title p {
      color: var(--muted);
      margin-bottom: 0;
    }

    .fight-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 16px;
    }

    .fighters {
      display: grid;
      grid-template-columns: 1fr auto 1fr;
      gap: 10px;
      align-items: center;
      margin-bottom: 14px;
    }

    .fighter-name {
      font-size: 20px;
      font-weight: 700;
    }

    .fighter-side:last-child {
      text-align: right;
    }

    .vs {
      color: var(--muted);
      font-weight: 700;
      letter-spacing: 0.08em;
    }

    .winner-pill,
    .actual-pill,
    .error-pill {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 12px;
    }

    .winner-pill { background: rgba(124,58,237,0.2); color: #ddd6fe; }
    .actual-pill { background: rgba(34,197,94,0.18); color: #bbf7d0; }
    .error-pill { background: rgba(239,68,68,0.18); color: #fecaca; }

    .probability-row {
      margin: 10px 0;
    }

    .odds-form {
      margin: 18px 0 24px;
      background: rgba(10, 15, 30, 0.65);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 18px;
    }

    .status-note {
      margin-top: 14px;
      padding: 12px 14px;
      border-radius: 12px;
      background: rgba(59, 130, 246, 0.12);
      color: #dbeafe;
      border: 1px solid rgba(59, 130, 246, 0.25);
      font-size: 14px;
    }

    .odds-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
      margin-top: 14px;
    }

    .odds-card {
      background: #0d152b;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 14px;
    }

    .odds-card label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }

    .tier-box {
      border-radius: 16px;
      padding: 14px;
      margin-bottom: 14px;
      border: 1px solid var(--border);
    }

    .tier-box h3 {
      margin-bottom: 6px;
    }

    .tier-box p {
      color: var(--text);
      opacity: 0.92;
      margin-bottom: 0;
    }

    .tier-elite { background: rgba(34,197,94,0.16); }
    .tier-strong { background: rgba(59,130,246,0.16); }
    .tier-volatile { background: rgba(245,158,11,0.16); }
    .tier-fragile { background: rgba(249,115,22,0.16); }
    .tier-trap { background: rgba(239,68,68,0.16); }
    .tier-live { background: rgba(16,185,129,0.16); }
    .tier-punch { background: rgba(168,85,247,0.16); }
    .tier-dead { background: rgba(107,114,128,0.22); }

    .probability-meta {
      display: flex;
      justify-content: space-between;
      font-size: 14px;
      color: var(--muted);
      margin-bottom: 6px;
    }

    .bar {
      width: 100%;
      height: 10px;
      border-radius: 999px;
      background: #0a1224;
      overflow: hidden;
      border: 1px solid var(--border);
    }

    .bar-fill {
      height: 100%;
      background: linear-gradient(135deg, var(--accent), #2563eb);
    }

    .fight-meta {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
      color: var(--muted);
      font-size: 13px;
    }

    .empty-state {
      padding: 28px;
      background: rgba(10, 15, 30, 0.65);
      border: 1px solid var(--border);
      border-radius: 20px;
      color: var(--muted);
    }

    @media (max-width: 900px) {
      .hero-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">UFC betting model</div>
          <h1>Fight predictions for the next UFC card</h1>
          <p>
            This landing page reads the next scheduled card export and shows model picks, confidence,
            and quick access to individual fight prediction pages.
          </p>
          <div class="sub-links">
            <a class="link-btn" href="fighters.php">View Fighters</a>
            <a class="link-btn" href="ufc.php?fighterA=Khamzat+Chimaev&fighterB=Sean+Strickland">Open Single Fight View</a>
          </div>
        </div>

        <div class="quick-form">
          <h3>Quick prediction lookup</h3>
          <form method="GET" action="ufc.php">
            <label for="fighterA">Fighter A</label>
            <input type="text" id="fighterA" name="fighterA" required>

            <label for="fighterB">Fighter B</label>
            <input type="text" id="fighterB" name="fighterB" required>

            <button type="submit">Predict Fight</button>
          </form>
        </div>
      </div>
    </section>

    <section>
      <div class="section-title">
        <div>
          <h2><?= htmlspecialchars($eventName) ?></h2>
          <p><?= $eventDate ? htmlspecialchars($eventDate) : 'No event date available' ?></p>
        </div>
      </div>

      <form class="odds-form" method="POST">
        <h3>Betting lines to model edge</h3>
        <p style="color: var(--muted); margin-bottom: 0;">
          Enter American odds for each side. The page converts the line to implied probability,
          compares it to model win probability, and assigns your tier automatically.
        </p>
        <p style="color: var(--muted); margin-top: 10px; margin-bottom: 0;">
          Manual entry is preferred when possible because it reflects the exact sportsbook price you are seeing right now.
        </p>
        <?php if ($dbStatusMessage): ?>
          <div class="status-note"><?= htmlspecialchars($dbStatusMessage) ?></div>
        <?php endif; ?>
        <div class="odds-grid">
          <?php foreach ($fights as $fight): ?>
            <?php $fightKey = buildFightKey($fight); ?>
            <div class="odds-card">
              <strong><?= htmlspecialchars($fight['fighterA']) ?> vs <?= htmlspecialchars($fight['fighterB']) ?></strong>
              <div style="margin-top: 10px;">
                <label for="<?= $fightKey ?>_a"><?= htmlspecialchars($fight['fighterA']) ?> odds</label>
                <input type="number" id="<?= $fightKey ?>_a" name="odds[<?= $fightKey ?>][fighterA]" value="<?= htmlspecialchars((string)($fight['fighterA_odds'] ?? '')) ?>" placeholder="-145 or +130">
              </div>
              <div style="margin-top: 10px;">
                <label for="<?= $fightKey ?>_b"><?= htmlspecialchars($fight['fighterB']) ?> odds</label>
                <input type="number" id="<?= $fightKey ?>_b" name="odds[<?= $fightKey ?>][fighterB]" value="<?= htmlspecialchars((string)($fight['fighterB_odds'] ?? '')) ?>" placeholder="+120 or -110">
              </div>
            </div>
          <?php endforeach; ?>
        </div>
        <div style="margin-top: 16px;">
          <button type="submit">Update tiers</button>
        </div>
      </form>

      <?php if (empty($fights)): ?>
        <div class="empty-state">
          No fight card data found. Run the scraper first so the page can read data/nearest_event_fights.csv.
        </div>
      <?php else: ?>
        <div class="fight-grid">
          <?php foreach ($fights as $fight): ?>
            <article class="fight-card">
              <div class="fighters">
                <div class="fighter-side">
                  <div class="fighter-name"><?= htmlspecialchars($fight['fighterA']) ?></div>
                </div>
                <div class="vs">VS</div>
                <div class="fighter-side">
                  <div class="fighter-name"><?= htmlspecialchars($fight['fighterB']) ?></div>
                </div>
              </div>

              <?php if (isset($fight['prediction']['error'])): ?>
                <div class="error-pill"><?= htmlspecialchars($fight['prediction']['error']) ?></div>
              <?php else: ?>
                <?php if (isset($fight['tier'])): ?>
                  <div class="tier-box <?= htmlspecialchars($fight['tier']['class']) ?>">
                    <h3><?= htmlspecialchars($fight['tier']['label']) ?></h3>
                    <p><?= htmlspecialchars($fight['tier']['desc']) ?></p>
                  </div>
                <?php endif; ?>

                <div class="winner-pill">
                  Model pick: <?= htmlspecialchars($fight['prediction']['winner'] ?? 'N/A') ?>
                  <?php if (isset($fight['confidence'])): ?>
                    · <?= number_format((float)$fight['confidence'], 2) ?>% confidence
                  <?php endif; ?>
                </div>
              <?php endif; ?>

              <?php if (!empty($fight['winner'])): ?>
                <div class="actual-pill">Actual winner: <?= htmlspecialchars($fight['winner']) ?></div>
              <?php endif; ?>

              <div class="probability-row">
                <div class="probability-meta">
                  <span><?= htmlspecialchars($fight['fighterA']) ?></span>
                  <span><?= number_format(((float)($fight['fighterA_probability'] ?? 0)) * 100, 2) ?>%</span>
                </div>
                <div class="bar">
                  <div class="bar-fill" style="width: <?= max(0, min(100, ((float)($fight['fighterA_probability'] ?? 0)) * 100)) ?>%"></div>
                </div>
              </div>

              <div class="probability-row">
                <div class="probability-meta">
                  <span><?= htmlspecialchars($fight['fighterB']) ?></span>
                  <span><?= number_format(((float)($fight['fighterB_probability'] ?? 0)) * 100, 2) ?>%</span>
                </div>
                <div class="bar">
                  <div class="bar-fill" style="width: <?= max(0, min(100, ((float)($fight['fighterB_probability'] ?? 0)) * 100)) ?>%"></div>
                </div>
              </div>

              <div class="fight-meta">
                <div>Round: <?= htmlspecialchars((string)($fight['round_num'] ?? 'N/A')) ?></div>
                <div>Time: <?= htmlspecialchars($fight['time_in_round'] ?? 'N/A') ?></div>
                <div>Method: <?= htmlspecialchars($fight['method'] ?: 'Decision/Unknown') ?></div>
                <div>
                  A line: <?= htmlspecialchars(formatAmericanOdds($fight['fighterA_odds'] ?? '')) ?>
                  <?php if (isset($fight['fighterA_implied']) && $fight['fighterA_implied'] !== null): ?>
                    · <?= number_format($fight['fighterA_implied'] * 100, 2) ?>%
                  <?php endif; ?>
                </div>
                <div>
                  B line: <?= htmlspecialchars(formatAmericanOdds($fight['fighterB_odds'] ?? '')) ?>
                  <?php if (isset($fight['fighterB_implied']) && $fight['fighterB_implied'] !== null): ?>
                    · <?= number_format($fight['fighterB_implied'] * 100, 2) ?>%
                  <?php endif; ?>
                </div>
                <?php if (isset($fight['tier'])): ?>
                  <div>
                    Best side: <?= htmlspecialchars($fight['recommended_side']) ?>
                  </div>
                  <div>
                    Edge: <?= number_format((float)$fight['recommended_edge'], 2) ?>%
                  </div>
                <?php endif; ?>
                <div>
                  <a href="ufc.php?fighterA=<?= urlencode($fight['fighterA']) ?>&fighterB=<?= urlencode($fight['fighterB']) ?>" style="color:#c4b5fd; text-decoration:none; font-weight:700;">
                    View fight
                  </a>
                </div>
              </div>
            </article>
          <?php endforeach; ?>
        </div>
      <?php endif; ?>
    </section>
  </div>
</body>
</html>
