package academic

import (
	"context"
	"errors"
	"net/url"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKSelectionSendNeverReplaysUncertainResults(t *testing.T) {
	for _, response := range []string{"network", "invalid-json", `{"code":500,"msg":"操作失败"}`, `{"code":200,"msg":"已进入队列"}`, `{"code":500,"msg":"教学班已满"}`} {
		calls := 0
		requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
			calls++
			if input.ReadOnly || input.RefreshSession || input.Form.Get("secretVal") != "private" {
				t.Fatal(input)
			}
			if response == "network" {
				return nil, errors.New("network timeout")
			}
			return []byte(response), nil
		})
		prepared := PreparedJWXKSelection{path: "/xsxk/elective/clazz/add", form: url.Values{"clazzType": {"XGKC"}, "secretVal": {"private"}}}
		result := SendJWXKSelection(context.Background(), requester, "direct", prepared, true)
		if calls != 1 || result.State == "confirmed" {
			t.Fatal(result, calls)
		}
		if response == `{"code":200,"msg":"已进入队列"}` && (!result.Queued || result.State != "accepted") {
			t.Fatal(result)
		}
		if response == `{"code":500,"msg":"教学班已满"}` && (result.State != "rejected" || result.Failure != "FULL") {
			t.Fatal(result)
		}
	}
}

func TestJWXKSelectionRiskConfirmationRequiresAuthorization(t *testing.T) {
	for _, confirm := range []bool{false, true} {
		calls := 0
		requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
			calls++
			if calls == 1 {
				if input.Form.Get("isConfirm") != "" {
					t.Fatal(input)
				}
				return []byte(`{"code":301}`), nil
			}
			if calls != 2 || input.Form.Get("isConfirm") != "1" {
				t.Fatal(input, calls)
			}
			return []byte(`{"code":200}`), nil
		})
		form := url.Values{"clazzType": {"XGKC"}, "secretVal": {"private"}}
		result := SendJWXKSelection(context.Background(), requester, "direct", PreparedJWXKSelection{path: "/xsxk/elective/clazz/add", form: form}, confirm)
		if confirm && (calls != 2 || result.State != "accepted") || !confirm && (calls != 1 || !result.RequiresConfirmation) || form.Get("isConfirm") != "" {
			t.Fatal(result, calls, form)
		}
	}
}
