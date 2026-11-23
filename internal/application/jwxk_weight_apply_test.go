package application

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type weightApplyClient struct {
	testCampusClient
	weights                 map[string]int
	writes                  []string
	unknownDrop, unknownAdd bool
	deniedAdd               bool
	wrongTerm               bool
}

func (client *weightApplyClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	term := "2026-2027-1"
	if client.wrongTerm {
		term = "2026-2027-2"
	}
	switch input.Path {
	case "/xsxk/web/now":
		return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
	case "/xsxk/web/studentInfo", "/xsxk/elective/user":
		used := 0
		for _, value := range client.weights {
			used += value
		}
		return []byte(fmt.Sprintf(`{"code":200,"data":{"remainingWeight":%d,"totalWeight":30,"student":{"electiveBatchList":[{"code":"round","name":"轮次","schoolTerm":"%s","typeCode":"04","canSelect":"1","beginTime":"2020-01-01 00:00:00","endTime":"2030-01-01 00:00:00","clazzTypeList":["FANKC"]}]}}}`, 30-used, term)), nil
	case "/xsxk/volunteer/select":
		rows := []map[string]any{}
		for _, class := range []string{"A", "B"} {
			if weight, exists := client.weights[class]; exists {
				rows = append(rows, map[string]any{"KCH": class, "JXBID": class, "KRL": 10, "YXRS": 0, "QZXKRS": 20, "TRQZ": weight, "secretVal": "test-secret", "teachingClassType": "FANKC"})
			}
		}
		return json.Marshal(map[string]any{"code": 200, "data": rows})
	case "/xsxk/elective/select", "/xsxk/volunteer/xgxk/select", "/xsxk/elective/neu/deselect":
		return []byte(`{"code":200,"data":[]}`), nil
	case "/xsxk/elective/clazz/list":
		return []byte(`{"code":200,"data":{"total":2,"rows":[{"KCH":"A","JXBID":"A","KRL":10,"YXRS":0,"QZXKRS":20,"secretVal":"test-secret"},{"KCH":"B","JXBID":"B","KRL":10,"YXRS":0,"QZXKRS":20,"secretVal":"test-secret"}]}}`), nil
	case "/xsxk/elective/check":
		return []byte(`{"code":200,"data":["是否可选：可选"]}`), nil
	case "/xsxk/elective/neu/clazz/del":
		class := input.Form.Get("clazzId")
		client.writes = append(client.writes, "drop:"+class)
		if client.unknownDrop {
			return nil, errors.New("response lost")
		}
		delete(client.weights, class)
		return []byte(`{"code":200}`), nil
	case "/xsxk/elective/neu/clazz/weightAdd":
		class := input.Form.Get("clazzId")
		weight, _ := strconv.Atoi(input.Form.Get("weight"))
		client.writes = append(client.writes, "add:"+class)
		if client.deniedAdd {
			return []byte(`{"code":500,"msg":"课程已满"}`), nil
		}
		if client.unknownAdd {
			return nil, errors.New("response lost")
		}
		client.weights[class] = weight
		return []byte(`{"code":200}`), nil
	}
	return []byte(`{"code":200,"data":[]}`), nil
}

const weightApplyBody = `{"batch_code":"round","term_code":"2026-2027-1","items":[{"class_id":"A","course_code":"A","teaching_class_type":"FANKC","weight":25},{"class_id":"B","course_code":"B","teaching_class_type":"FANKC","weight":5}]}`

func TestWeightApplyExplicitZeroBidWithdrawsBeforeFundingOtherTarget(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student-a")
	client := &weightApplyClient{testCampusClient: testCampusClient{account: "student-a"}, weights: map[string]int{"A": 5, "B": 25}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	body := strings.Replace(weightApplyBody, `"weight":25`, `"weight":30`, 1)
	body = strings.Replace(body, `"weight":5`, `"weight":0,"withdraw_only":true`, 1)
	endpoint := "/api/course-selection/jwxk/weights/apply"
	response := apiRequest(handler, "POST", endpoint, strings.Replace(body, `,"withdraw_only":true`, "", 1), cookie)
	if response.Code != 422 || len(client.writes) != 0 {
		t.Fatal("implicit zero withdrawal accepted", response)
	}
	response = apiRequest(handler, "POST", endpoint, body, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"completed":true`) || strings.Join(client.writes, ",") != "drop:A,drop:B,add:A" || client.weights["A"] != 30 {
		t.Fatal(response, client.writes, client.weights)
	}
	response = apiRequest(handler, "POST", endpoint, body, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"completed":true`) || len(client.writes) != 3 {
		t.Fatal("completed zero withdrawal replayed", response, client.writes)
	}
}

func weightTestPlan(t *testing.T) academic.JWXKSavedPlan {
	t.Helper()
	var plan academic.JWXKSavedPlan
	if err := json.Unmarshal([]byte(weightApplyBody), &plan); err != nil {
		t.Fatal(err)
	}
	return plan
}

func TestWeightApplyBoundedStepsPersistAndReadOnlyRecoveryNeverAdvancesWrites(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	client := &weightApplyClient{testCampusClient: testCampusClient{account: "student-a"}, weights: map[string]int{"A": 5, "B": 25}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	loginCookie(t, handler, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	runtime := api.accounts.Runtime(session)
	plan := weightTestPlan(t)
	for expected := 1; expected <= 4; expected++ {
		result, err := api.advanceJWXKWeights(ctx, runtime, session, "direct", plan, 1, false)
		if err != nil || result["completed"] != false || result["yielded"] != true || len(client.writes) != expected {
			t.Fatal("bounded step did not yield after one confirmed operation", result, err, client.writes)
		}
		if expected < 4 {
			result, err = api.advanceJWXKWeights(ctx, runtime, session, "direct", plan, 1, true)
			if err != nil || result["completed"] != false || result["yielded"] != false || len(client.writes) != expected {
				t.Fatal("read-only recovery started unsent operation", result, err, client.writes)
			}
		}
	}
	result, err := api.advanceJWXKWeights(ctx, runtime, session, "direct", plan, 1, true)
	if err != nil || result["completed"] != true || len(client.writes) != 4 || strings.Join(client.writes, ",") != "drop:A,drop:B,add:A,add:B" {
		t.Fatal(result, err, client.writes)
	}
}

func TestWeightApplyReadOnlyRecoversUnknownDropWithoutStartingNext(t *testing.T) {
	checkRestoredWeightStep(t, false)
}

func TestWeightApplyReadOnlyRecoversUnknownAddWithoutStartingNext(t *testing.T) {
	checkRestoredWeightStep(t, true)
}

func checkRestoredWeightStep(t *testing.T, adding bool) {
	t.Helper()
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	client := &weightApplyClient{testCampusClient: testCampusClient{account: "student-a"}, weights: map[string]int{"A": 5, "B": 25}, unknownDrop: !adding, unknownAdd: adding}
	expectedWrite := "drop:A"
	if adding {
		client.weights = map[string]int{}
		expectedWrite = "add:A"
	}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	loginCookie(t, handler, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	runtime := api.accounts.Runtime(session)
	plan := weightTestPlan(t)
	result, err := api.advanceJWXKWeights(ctx, runtime, session, "direct", plan, 1, false)
	if err != nil || result["completed"] != false || result["yielded"] != false || strings.Join(client.writes, ",") != expectedWrite {
		t.Fatal(result, err, client.writes)
	}
	root := t.TempDir()
	archive, destination := filepath.Join(root, "snapshot.age"), filepath.Join(root, "restored")
	if err := api.database.Backup(ctx, archive, "synthetic-test-password"); err != nil {
		t.Fatal(err)
	}
	if err := store.RestoreBackup(ctx, archive, destination, "synthetic-test-password"); err != nil {
		t.Fatal(err)
	}
	database, err := store.Open(destination)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	accounts, err := NewAccounts(database, "remote-multi")
	if err != nil {
		t.Fatal(err)
	}
	defer accounts.Close()
	reconnected := &weightApplyClient{testCampusClient: testCampusClient{account: "student-a"}, weights: client.weights}
	accounts.factory = func(string) (CampusClient, error) { return reconnected, nil }
	api = NewAPI(database, accounts, "test")
	api.gatewayRequired = false
	router := http.NewServeMux()
	api.Register(router)
	loginCookie(t, api.Boundary(router), "student-a")
	session.Generation, err = database.Generation(ctx, session.Account)
	if err != nil || session.Generation == generation {
		t.Fatal("restore did not revoke old generation", err)
	}
	runtime = accounts.Runtime(session)
	if pending, err := api.pendingWeightApplication(ctx, session, plan.Batch, plan.Term); err != nil || pending["recovery"] != true {
		t.Fatal("missing restored weight plan", pending, err)
	}
	if adding {
		reconnected.weights["A"] = 25
	} else {
		delete(reconnected.weights, "A")
	}
	result, err = api.advanceJWXKWeights(ctx, runtime, session, "direct", plan, 1, true)
	if err != nil || result["yielded"] != true || len(reconnected.writes) != 0 {
		t.Fatal("unknown operation was replayed", result, err, client.writes)
	}
	result, err = api.advanceJWXKWeights(ctx, runtime, session, "direct", plan, 1, true)
	if err != nil || result["completed"] != false || result["yielded"] != false || len(reconnected.writes) != 0 {
		t.Fatal("reconciliation continued with next withdrawal", result, err, client.writes)
	}
}

func TestWeightApplyWithdrawsBeforeReallocationAndIsIdempotent(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student-a")
	client := &weightApplyClient{testCampusClient: testCampusClient{account: "student-a"}, weights: map[string]int{"A": 5, "B": 25}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	endpoint := "/api/course-selection/jwxk/weights/apply"
	response := apiRequest(handler, "POST", endpoint, weightApplyBody, nil)
	if response.Code != 401 || len(client.writes) != 0 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint, weightApplyBody, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"completed":true`) || strings.Join(client.writes, ",") != "drop:A,drop:B,add:A,add:B" {
		t.Fatal(response, client.writes)
	}
	response = apiRequest(handler, "POST", endpoint, weightApplyBody, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"completed":true`) || len(client.writes) != 4 {
		t.Fatal("repeat application wrote again", response, client.writes)
	}
	for _, method := range []string{"PUT", "DELETE"} {
		response = apiRequest(handler, method, "/api/documents/weight-applications/round", `{"data":{},"revision":0}`, cookie)
		if response.Code != 403 {
			t.Fatal("journal client mutation allowed", response)
		}
	}
}

func TestWeightApplyUncertainStepsStopAndResumeWithoutReplay(t *testing.T) {
	for _, stage := range []string{"drop", "add"} {
		t.Run(stage, func(t *testing.T) {
			api, handler := testApplication(t, "remote-multi")
			ctx := context.Background()
			api.database.AddAccount(ctx, "student-a")
			client := &weightApplyClient{testCampusClient: testCampusClient{account: "student-a"}, weights: map[string]int{"A": 5, "B": 25}, unknownDrop: stage == "drop", unknownAdd: stage == "add"}
			api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
			cookie := loginCookie(t, handler, "student-a")
			endpoint := "/api/course-selection/jwxk/weights/apply"
			response := apiRequest(handler, "POST", endpoint, weightApplyBody, cookie)
			if response.Code != 200 || !strings.Contains(response.Body.String(), `"completed":false`) {
				t.Fatal(response)
			}
			count := len(client.writes)
			cancelled := apiRequest(handler, "POST", "/api/course-selection/jwxk/weights/application/cancel", `{"batch_code":"round"}`, cookie)
			if cancelled.Code != 409 || len(client.writes) != count {
				t.Fatal("uncertain application cancelled", cancelled)
			}
			recovery := apiRequest(handler, "GET", "/api/course-selection/jwxk/weights/config?term_code=2026-2027-1&batch_code=round", "", cookie)
			if recovery.Code != 200 || !strings.Contains(recovery.Body.String(), `"pending_application":{"`) || !strings.Contains(recovery.Body.String(), `"recovery":true`) || len(client.writes) != count {
				t.Fatal("pending application cannot be recovered", recovery)
			}
			response = apiRequest(handler, "POST", endpoint, weightApplyBody, cookie)
			if response.Code != 200 || len(client.writes) != count {
				t.Fatal("uncertain operation replayed", response, client.writes)
			}
			response = apiRequest(handler, "POST", endpoint, strings.ReplaceAll(weightApplyBody, `"weight":25`, `"weight":20`), cookie)
			if response.Code != 409 || len(client.writes) != count {
				t.Fatal("uncertain application replaced", response)
			}
			if stage == "drop" {
				delete(client.weights, "A")
				client.unknownDrop = false
			} else {
				client.weights["A"] = 25
				client.unknownAdd = false
			}
			generation, _ := api.database.Generation(ctx, "student-a")
			journal, err := api.database.Read(ctx, "student-a", jwxkWeightApplicationNamespace, "round")
			if err != nil || journal.Revision < 1 {
				t.Fatal(err, journal)
			}
			freshAPI := NewAPI(api.database, api.accounts, "test")
			plan := weightTestPlan(t)
			session := store.Session{Account: "student-a", Generation: generation}
			result, err := freshAPI.applyJWXKWeights(ctx, api.accounts.Runtime(session), session, "direct", plan)
			if err != nil || result["completed"] != true || strings.Join(client.writes, ",") != "drop:A,drop:B,add:A,add:B" {
				t.Fatal(err, result, client.writes)
			}
		})
	}
}

func TestWeightApplyKnownFailureCanBeEndedAndReplanned(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student-a")
	client := &weightApplyClient{testCampusClient: testCampusClient{account: "student-a"}, weights: map[string]int{"A": 5, "B": 25}, deniedAdd: true}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	response := apiRequest(handler, "POST", "/api/course-selection/jwxk/weights/apply", weightApplyBody, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"completed":false`) || len(client.writes) != 3 {
		t.Fatal(response, client.writes)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/weights/application/cancel", `{"batch_code":"round"}`, cookie)
	if response.Code != 200 || len(client.writes) != 3 {
		t.Fatal(response, client.writes)
	}
	response = apiRequest(handler, "GET", "/api/course-selection/jwxk/weights/config?term_code=2026-2027-1&batch_code=round", "", cookie)
	if !strings.Contains(response.Body.String(), `"pending_application":null`) {
		t.Fatal(response)
	}
	client.deniedAdd = false
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/weights/apply", strings.ReplaceAll(weightApplyBody, `"weight":25`, `"weight":20`), cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"completed":true`) || len(client.writes) != 5 {
		t.Fatal(response, client.writes)
	}
}

func TestWeightApplyInvalidScopeBudgetAndTermCannotWithdraw(t *testing.T) {
	for _, scenario := range []string{"scope", "budget", "duplicate", "term"} {
		t.Run(scenario, func(t *testing.T) {
			api, handler := testApplication(t, "remote-multi")
			api.database.AddAccount(context.Background(), "student-a")
			client := &weightApplyClient{testCampusClient: testCampusClient{account: "student-a"}, weights: map[string]int{"A": 5, "B": 25}, wrongTerm: scenario == "term"}
			api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
			cookie := loginCookie(t, handler, "student-a")
			body := weightApplyBody
			switch scenario {
			case "scope":
				body = strings.ReplaceAll(body, "FANKC", "ALLKC")
			case "budget":
				body = strings.ReplaceAll(body, `"weight":25`, `"weight":30`)
			case "duplicate":
				body = strings.ReplaceAll(body, `"course_code":"B"`, `"course_code":"A"`)
			}
			response := apiRequest(handler, "POST", "/api/course-selection/jwxk/weights/apply", body, cookie)
			if response.Code < 400 || len(client.writes) != 0 {
				t.Fatal(response, client.writes)
			}
		})
	}
}
