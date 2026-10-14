-- Chronotype comparison: cohort A, cohort B and A minus B (reads only gold_chronotype_cohort).
-- Per metric, only participants whose participant-level mean is not NULL contribute:
--   _mean         mean of the participant-level means
--   _participants number of contributing participants
--   _stddev       sample standard deviation (n - 1) across those means, NULL below 2 participants
--   _valid_days   sum of the metric's valid-day column over the same participants
-- The A_minus_B row holds only the difference of the unrounded cohort means (positive = A higher).
-- Values are rounded to 2 decimals as the last step. No significance claim is made.
WITH cohorts AS (
    SELECT 'A' AS group_label
    UNION ALL
    SELECT 'B'
),
stats AS (
    SELECT
        c.group_label,
        avg(p.sleep_total_min_mean) AS sleep_total_min_avg,
        count(p.sleep_total_min_mean) AS sleep_total_min_n,
        stddev_samp(p.sleep_total_min_mean) AS sleep_total_min_sd,
        coalesce(sum(CASE WHEN p.sleep_total_min_mean IS NOT NULL THEN p.sleep_valid_days END), 0)::BIGINT
            AS sleep_total_min_days,
        avg(p.sleep_efficiency_pct_mean) AS sleep_efficiency_pct_avg,
        count(p.sleep_efficiency_pct_mean) AS sleep_efficiency_pct_n,
        stddev_samp(p.sleep_efficiency_pct_mean) AS sleep_efficiency_pct_sd,
        coalesce(sum(CASE WHEN p.sleep_efficiency_pct_mean IS NOT NULL THEN p.sleep_stage_valid_days END), 0)::BIGINT
            AS sleep_efficiency_pct_days,
        avg(p.deep_sleep_pct_mean) AS deep_sleep_pct_avg,
        count(p.deep_sleep_pct_mean) AS deep_sleep_pct_n,
        stddev_samp(p.deep_sleep_pct_mean) AS deep_sleep_pct_sd,
        coalesce(sum(CASE WHEN p.deep_sleep_pct_mean IS NOT NULL THEN p.sleep_stage_valid_days END), 0)::BIGINT
            AS deep_sleep_pct_days,
        avg(p.steps_daily_mean) AS steps_daily_avg,
        count(p.steps_daily_mean) AS steps_daily_n,
        stddev_samp(p.steps_daily_mean) AS steps_daily_sd,
        coalesce(sum(CASE WHEN p.steps_daily_mean IS NOT NULL THEN p.steps_valid_days END), 0)::BIGINT
            AS steps_daily_days,
        avg(p.hr_daily_mean_bpm) AS hr_daily_bpm_avg,
        count(p.hr_daily_mean_bpm) AS hr_daily_bpm_n,
        stddev_samp(p.hr_daily_mean_bpm) AS hr_daily_bpm_sd,
        coalesce(sum(CASE WHEN p.hr_daily_mean_bpm IS NOT NULL THEN p.hr_valid_days END), 0)::BIGINT
            AS hr_daily_bpm_days
    FROM cohorts AS c
    LEFT JOIN gold_chronotype_cohort AS p ON p.chronotype_cohort = c.group_label
    GROUP BY c.group_label
),
combined AS (
    SELECT
        1 AS sort_key,
        group_label,
        ROUND(sleep_total_min_avg, 2) AS sleep_total_min_mean,
        sleep_total_min_n AS sleep_total_min_participants,
        ROUND(sleep_total_min_sd, 2) AS sleep_total_min_stddev,
        sleep_total_min_days AS sleep_total_min_valid_days,
        ROUND(sleep_efficiency_pct_avg, 2) AS sleep_efficiency_pct_mean,
        sleep_efficiency_pct_n AS sleep_efficiency_pct_participants,
        ROUND(sleep_efficiency_pct_sd, 2) AS sleep_efficiency_pct_stddev,
        sleep_efficiency_pct_days AS sleep_efficiency_pct_valid_days,
        ROUND(deep_sleep_pct_avg, 2) AS deep_sleep_pct_mean,
        deep_sleep_pct_n AS deep_sleep_pct_participants,
        ROUND(deep_sleep_pct_sd, 2) AS deep_sleep_pct_stddev,
        deep_sleep_pct_days AS deep_sleep_pct_valid_days,
        ROUND(steps_daily_avg, 2) AS steps_daily_mean,
        steps_daily_n AS steps_daily_participants,
        ROUND(steps_daily_sd, 2) AS steps_daily_stddev,
        steps_daily_days AS steps_daily_valid_days,
        ROUND(hr_daily_bpm_avg, 2) AS hr_daily_bpm_mean,
        hr_daily_bpm_n AS hr_daily_bpm_participants,
        ROUND(hr_daily_bpm_sd, 2) AS hr_daily_bpm_stddev,
        hr_daily_bpm_days AS hr_daily_bpm_valid_days
    FROM stats
    UNION ALL
    SELECT
        2 AS sort_key,
        'A_minus_B' AS group_label,
        ROUND(a.sleep_total_min_avg - b.sleep_total_min_avg, 2),
        NULL::BIGINT, NULL::DOUBLE, NULL::BIGINT,
        ROUND(a.sleep_efficiency_pct_avg - b.sleep_efficiency_pct_avg, 2),
        NULL::BIGINT, NULL::DOUBLE, NULL::BIGINT,
        ROUND(a.deep_sleep_pct_avg - b.deep_sleep_pct_avg, 2),
        NULL::BIGINT, NULL::DOUBLE, NULL::BIGINT,
        ROUND(a.steps_daily_avg - b.steps_daily_avg, 2),
        NULL::BIGINT, NULL::DOUBLE, NULL::BIGINT,
        ROUND(a.hr_daily_bpm_avg - b.hr_daily_bpm_avg, 2),
        NULL::BIGINT, NULL::DOUBLE, NULL::BIGINT
    FROM stats AS a
    CROSS JOIN stats AS b
    WHERE a.group_label = 'A' AND b.group_label = 'B'
)
SELECT * EXCLUDE (sort_key)
FROM combined
ORDER BY sort_key, group_label;
