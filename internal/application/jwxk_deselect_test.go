package application

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type jwxkRemovalClient struct {
	jwxkMutationClient
	removals         int
	uncertainRemoval bool
	zeroRecord       bool
}

func (client *jwxkRemovalClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	if input.Path == "/xsxk/elective/select" && client.visible {
		if client.zeroRecord {
			return []byte(`{"code":200,"data":[{"KCH":"C1","JXBID":"class","KRL":0,"YXRS":0,"secretVal":"private","teachingClassType":"XGKC"}]}`), nil
		}
		return []byte(`{"code":200,"data":[{"KCH":"C1","JXBID":"class","KRL":10,"YXRS":1,"secretVal":"private","teachingClassType":"XGKC","electiveBatchCode":"earlier-round"}]}`), nil
	}
	if input.Path == "/xsxk/elective/neu/clazz/del" {
		client.removals++
		if input.ReadOnly || input.Form.Get("source") != "yxkcyx" || input.Form.Get("crossBatch") != "1" || input.Form.Get("secretVal") != "private" {
			return nil, errors.New("wrong drop protocol")
		}
		if client.uncertainRemoval {
			return nil, errors.New("response lost")
		}
		client.visible = false
		return []byte(`{"code":200}`), nil
	}
	return client.jwxkMutationClient.RequestService(ctx, input)
}

func TestJWXKConfirmedRemovalAllowsReselection(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student-a"); err != nil {
		t.Fatal(err)
	}
	client := &jwxkRemovalClient{jwxkMutationClient: jwxkMutationClient{testCampusClient: testCampusClient{account: "student-a"}}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	selection := `{"batch_code":"round","teaching_class_type":"XGKC","class_id":"class","course_code":"C1"}`
	removal := `{"batch_code":"round","class_id":"class","selection_source":"yxkcyx"}`
	for cycle := 1; cycle <= 2; cycle++ {
		response := apiRequest(handler, "POST", "/api/course-selection/jwxk/courses/select", selection, cookie)
		if response.Code != 200 || client.writes != cycle || !strings.Contains(response.Body.String(), `"state":"confirmed"`) {
			t.Fatal(response, client.writes)
		}
		response = apiRequest(handler, "POST", "/api/course-selection/jwxk/courses/deselect", removal, cookie)
		if response.Code != 200 || client.removals != cycle || !strings.Contains(response.Body.String(), `"state":"confirmed"`) {
			t.Fatal(response, client.removals)
		}
	}
}

func TestJWXKUnknownRemovalBlocksReselectUntilVerified(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student-a")
	client := &jwxkRemovalClient{jwxkMutationClient: jwxkMutationClient{testCampusClient: testCampusClient{account: "student-a"}, visible: true}, uncertainRemoval: true}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	body := `{"batch_code":"round","class_id":"class","selection_source":"yxkcyx"}`
	endpoint := "/api/course-selection/jwxk/courses/deselect"
	if response := apiRequest(handler, "POST", endpoint, body, nil); response.Code != 401 || client.removals != 0 {
		t.Fatal(response)
	}
	response := apiRequest(handler, "POST", endpoint, body, cookie)
	if response.Code != 200 || client.removals != 1 || !strings.Contains(response.Body.String(), `"state":"unknown"`) {
		t.Fatal(response)
	}
	client.visible = false
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/courses/select", `{"batch_code":"round","teaching_class_type":"XGKC","class_id":"class","course_code":"C1"}`, cookie)
	if response.Code != 200 || client.writes != 0 || !strings.Contains(response.Body.String(), "退课结果尚未确认") {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint, body, cookie)
	if response.Code != 200 || client.removals != 1 || !strings.Contains(response.Body.String(), `"state":"confirmed"`) {
		t.Fatal(response)
	}
}

func TestJWXKRemovalRejectsZeroRecordAndWrongSource(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student-a")
	client := &jwxkRemovalClient{jwxkMutationClient: jwxkMutationClient{testCampusClient: testCampusClient{account: "student-a"}, visible: true}, zeroRecord: true}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	for _, source := range []string{"yxkcyx", "fakcyx"} {
		response := apiRequest(handler, "POST", "/api/course-selection/jwxk/courses/deselect", `{"batch_code":"round","class_id":"class","selection_source":"`+source+`"}`, cookie)
		if response.Code == 200 || client.removals != 0 {
			t.Fatal(response)
		}
	}
}
