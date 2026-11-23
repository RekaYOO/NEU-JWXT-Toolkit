package application

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type jwxkMutationClient struct {
	testCampusClient
	writes    int
	visible   bool
	uncertain bool
}

func (client *jwxkMutationClient) RequestService(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
	switch input.Path {
	case "/xsxk/web/now":
		return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
	case "/xsxk/web/studentInfo", "/xsxk/elective/user":
		return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","name":"轮次","schoolTerm":"term","typeCode":"02","canSelect":"1","beginTime":"2020-01-01 00:00:00","endTime":"2030-01-01 00:00:00","clazzTypeList":["XGKC"]}]}}}`), nil
	case "/xsxk/elective/select":
		if client.visible {
			return []byte(`{"code":200,"data":[{"KCH":"C1","JXBID":"class","KRL":10,"YXRS":1,"secretVal":"private"}]}`), nil
		}
		return []byte(`{"code":200,"data":[]}`), nil
	case "/xsxk/volunteer/select", "/xsxk/volunteer/xgxk/select", "/xsxk/elective/neu/deselect":
		return []byte(`{"code":200,"data":[]}`), nil
	case "/xsxk/elective/clazz/list":
		return []byte(`{"code":200,"data":{"rows":[{"KCH":"C1","JXBID":"class","secretVal":"private"}]}}`), nil
	case "/xsxk/elective/check":
		return []byte(`{"code":200,"data":["是否可选：可选"]}`), nil
	case "/xsxk/elective/clazz/add":
		client.writes++
		if client.uncertain {
			return nil, errors.New("connection lost")
		}
		client.visible = true
		return []byte(`{"code":200,"msg":"成功"}`), nil
	}
	return nil, errors.New("unexpected service request")
}

func TestJWXKSelectConfirmsOfficialResultAndPreventsReplay(t *testing.T) {
	for _, uncertain := range []bool{false, true} {
		api, handler := testApplication(t, "remote-multi")
		if err := api.database.AddAccount(context.Background(), "student-a"); err != nil {
			t.Fatal(err)
		}
		client := &jwxkMutationClient{testCampusClient: testCampusClient{account: "student-a"}, uncertain: uncertain}
		api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
		cookie := loginCookie(t, handler, "student-a")
		endpoint := "/api/course-selection/jwxk/courses/select"
		body := `{"batch_code":"round","teaching_class_type":"XGKC","class_id":"class","course_code":"C1","preflight_verified":true}`
		if response := apiRequest(handler, "POST", endpoint, body, nil); response.Code != 401 || client.writes != 0 {
			t.Fatal(response)
		}
		response := apiRequest(handler, "POST", endpoint, body, cookie)
		if response.Code != 200 || client.writes != 1 || strings.Contains(response.Body.String(), "private") {
			t.Fatal(response, client.writes)
		}
		if uncertain && !strings.Contains(response.Body.String(), `"state":"unknown"`) || !uncertain && !strings.Contains(response.Body.String(), `"state":"confirmed"`) {
			t.Fatal(response)
		}
		response = apiRequest(handler, "POST", endpoint, body, cookie)
		if response.Code != 200 || client.writes != 1 {
			t.Fatal("operation replayed", response, client.writes)
		}
		client.visible = true
		response = apiRequest(handler, "POST", endpoint, body, cookie)
		if response.Code != 200 || client.writes != 1 || !strings.Contains(response.Body.String(), `"state":"confirmed"`) {
			t.Fatal("late official result not reconciled", response, client.writes)
		}
		if response := apiRequest(handler, "DELETE", "/api/documents/selection-attempts/key", `{"revision":1}`, cookie); response.Code != 403 {
			t.Fatal("journal may be deleted", response)
		}
	}
}
