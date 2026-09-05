-- V5：区分首次上传与文件更新，并允许知识库表达“部分异常”。
ALTER TABLE operations DROP CONSTRAINT IF EXISTS operations_operation_type_check;
ALTER TABLE operations ADD CONSTRAINT operations_operation_type_check
    CHECK (operation_type IN ('index_build','sync_run','file_upload','file_update',
                              'document_reprocess','index_validation','index_activation'));

ALTER TABLE document_processing_runs
    DROP CONSTRAINT IF EXISTS document_processing_runs_processing_type_check;
ALTER TABLE document_processing_runs
    ADD CONSTRAINT document_processing_runs_processing_type_check
    CHECK (processing_type IN ('file_upload','file_update','reparse','reindex','restore'));
