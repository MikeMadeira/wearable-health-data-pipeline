-- Study-wide metrics from gold_clinical_daily (reads only that table).
-- A valid day of a metric is a row whose source value is not NULL. Each participant's
-- average is the mean of their valid-day values; the study value is the mean of those
-- participant averages (not the pooled mean of all days). No coverage threshold and no
-- cohort filter: every participant in gold_clinical_daily with a valid day counts.
-- Rounded to 2 decimals as the last step.
WITH per_participant AS (
    SELECT
        participant_id,
        avg(total_steps) AS steps_avg,
        count(total_steps) AS steps_days,
        avg(total_sleep_min) AS sleep_avg,
        count(total_sleep_min) AS sleep_days,
        avg(hr_mean_bpm) AS hr_avg,
        count(hr_mean_bpm) AS hr_days
    FROM gold_clinical_daily
    GROUP BY participant_id
),
metrics AS (
    SELECT
        1 AS sort_key,
        'steps_per_day' AS metric,
        count(steps_avg) AS participants,
        coalesce(sum(steps_days), 0)::BIGINT AS valid_days,
        ROUND(avg(steps_avg), 2) AS mean_of_participant_averages
    FROM per_participant
    WHERE steps_days > 0
    UNION ALL
    SELECT
        2,
        'sleep_total_min',
        count(sleep_avg),
        coalesce(sum(sleep_days), 0)::BIGINT,
        ROUND(avg(sleep_avg), 2)
    FROM per_participant
    WHERE sleep_days > 0
    UNION ALL
    SELECT
        3,
        'hr_mean_bpm',
        count(hr_avg),
        coalesce(sum(hr_days), 0)::BIGINT,
        ROUND(avg(hr_avg), 2)
    FROM per_participant
    WHERE hr_days > 0
)
SELECT metric, participants, valid_days, mean_of_participant_averages
FROM metrics
ORDER BY sort_key;
