-- Bad Case 回归必须由真实运行产出，不能再由前端布尔量直接声明通过。

ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_evaluation_type_check;
ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_evaluation_type_check
    CHECK (evaluation_type IN (
        'retrieval','answer','pipeline','security','acceptance','intent_routing','regression'
    ));

CREATE INDEX evaluation_runs_regression_case_idx
    ON evaluation_runs ((metrics->>'case_id'), run_at DESC)
    WHERE evaluation_type='regression';
