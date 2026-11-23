package application

import (
	"context"
	"errors"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func importedOperationKey(operation academic.JWXKImportedOperation) (string, error) {
	if operation.Action == "select" && operation.Selection != nil && operation.Selection.Valid() && operation.Removal == nil {
		return jwxkSelectionKey(*operation.Selection), nil
	}
	if operation.Action == "drop" && operation.Removal != nil && operation.Removal.Valid() && operation.Selection == nil {
		return jwxkDeselectionKey(operation.Removal.Batch, operation.Removal.Class), nil
	}
	return "", errors.New("invalid imported operation")
}

func (api *API) verifyImportedTaskOperation(ctx context.Context, runtime *Runtime, session store.Session, task *academic.JWXKAutomationTask, mode string) error {
	operation := task.ImportedOperations[0]
	key, err := importedOperationKey(operation)
	if err != nil {
		return err
	}
	attempt, err := api.database.SelectionAttempt(ctx, session, key)
	if err != nil {
		return err
	}
	class := ""
	if operation.Selection != nil {
		class = operation.Selection.Class
	} else {
		class = operation.Removal.Class
	}
	if attempt.Batch != task.Batch || attempt.Term != task.Term || attempt.Class != class || attempt.Course != operation.Course {
		return store.ErrConflict
	}
	verified := false
	term := ""
	if operation.Action == "select" {
		verified, term, err = academic.VerifyJWXKSelection(ctx, runtime, mode, *operation.Selection)
	} else {
		verified, term, err = academic.VerifyJWXKDeselection(ctx, runtime, mode, *operation.Removal)
	}
	if err != nil {
		return err
	}
	if !verified || term != task.Term {
		task.Status, task.Desired, task.Message = "needs_review", "paused", "旧版提交结果仍未由学校确认；已暂停，不会重复发送，请核验后再启动"
		return nil
	}
	if err := api.database.FinishSelection(ctx, session, key, attempt, "confirmed"); err != nil {
		return err
	}
	if operation.Action == "drop" {
		if err := api.completeJWXKDeselection(ctx, runtime, session, *operation.Removal, attempt, term); err != nil {
			return err
		}
	}
	task.ImportedOperations = task.ImportedOperations[1:]
	task.Message = "旧版操作已核验，下一次检查继续处理其余任务"
	return api.database.InvalidateCache(ctx, session, "personal-timetable", "term:"+task.Term)
}
