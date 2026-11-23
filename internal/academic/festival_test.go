package academic

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestFestivalParticipationLabelsPaginationAndCertificateScope(t *testing.T) {
	body := `<div class="list_item"><div>活动名称：科学之光 类别：报告 活动状态：已结束 签到情况：已签到 签退情况：已签退 <a href="../../front/comp/info?id=54">团队</a></div><a href="/static/uploads/res/certificate/cert_17/a.png?t=123">证书</a></div><a href="?page=2">下一页</a>`
	rows, pages, err := ParseFestivalParticipation(body, "popscience")
	if err != nil || len(rows) != 1 || len(pages) != 1 {
		t.Fatal(rows, pages, err)
	}
	row := rows[0]
	if row.ID != "54" || row.Name != "科学之光" || row.SignIn != "已签到" || row.SignOut != "已签退" || row.Team != "团队" || row.CertificateURL != festivalOrigin+"/static/uploads/res/certificate/cert_17/a.png" || pages[0] != "/popscience/comp/ucenter/main/index?page=2" {
		t.Fatal(row, pages)
	}
	for _, bad := range []string{
		"https://evil.invalid/static/uploads/res/popsciencecert/a.png", "http://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/a.png", "/static/uploads/res/originalitycert/a.png", "/static/uploads/res/popsciencecert/a.svg", "/static/uploads/res/certificate/a.png", "/static/uploads/res/certificate/17/nested/a.png", "/static/uploads/res/certificate/17/a.png?download=1", "/static/uploads/res/certificate/17/a.png?t=bad", "/static/uploads/res/certificate/17/%2e%2e/a.png", "/static/uploads/res/certificate/17/%252e%252e.png", "https://user@cxcy.neu.edu.cn/static/uploads/res/certificate/17/a.png", "https://cxcy.neu.edu.cn:444/static/uploads/res/certificate/17/a.png",
	} {
		if result := FestivalCertificateURL(bad, "popscience"); result != "" {
			t.Fatal("unsafe certificate accepted", bad, result)
		}
	}
	for _, bad := range []string{"/static/uploads/res/certificate/17/nested/../a.png", "/static/uploads/res/certificate/17/%2e/a.png"} {
		if result := FestivalCertificateURL(bad, "popscience"); result != "" {
			t.Fatal("normalized traversal accepted", bad, result)
		}
	}
}

func TestFestivalCertificatesStayWithinTheirOwnRecord(t *testing.T) {
	body := `<div class="page"><div class="media_body"><h3><a href="../../front/comp/info?id=54">team</a></h3></div><aside><a href="/static/uploads/res/popsciencecert/unrelated.png">下载</a></aside></div>`
	rows, _, err := ParseFestivalParticipation(body, "popscience")
	if err != nil || len(rows) != 1 || rows[0].CertificateAvailable {
		t.Fatal("page certificate attached to unrelated record", rows, err)
	}
	body = `<div class="list_item"><div><a href="../../front/comp/info?id=1">one</a></div><button onclick="window.open('/static/uploads/res/popsciencecert/one.png')">下载</button></div><div class="list_item"><div><a href="../../front/comp/info?id=2">two</a></div><img data-src="/static/uploads/res/popsciencecert/two.webp"></div>`
	rows, _, err = ParseFestivalParticipation(body, "popscience")
	if err != nil || len(rows) != 2 || !strings.HasSuffix(rows[0].CertificateURL, "/one.png") || !strings.HasSuffix(rows[1].CertificateURL, "/two.webp") {
		t.Fatal(rows, err)
	}
}

func TestFestivalDetailPriorityPlainTextAndDate(t *testing.T) {
	body := `<header><div class="title">最新公告</div></header><div class="body_box"><div class="body_part1"><div class="title">科学探秘</div><div>类别：竞赛活动</div><div>类型：报告</div><div>活动时间：2025-04-20 08:30（2小时）</div><div>所属部门：体育部</div><div>活动地点：体育馆</div></div></div><div class="body_part2"><div class="p_title">活动简介</div><div class="content"><p>第一段</p><script>secret()</script><p>第二段</p></div></div>`
	row, err := ParseFestivalDetail(body, FestivalActivity{Name: "旧名称"})
	if err != nil || row.Name != "科学探秘" || row.StartTime == nil || *row.StartTime != "2025-04-20T08:30:00" || row.Duration != "2" || row.Department != "体育部" || row.Description != "第一段 第二段" {
		t.Fatal(row, err)
	}
}

type festivalRequester func(context.Context, campus.ServiceRequest) ([]byte, error)

func (request festivalRequester) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	return request(ctx, input)
}

func TestFestivalFetchFixedRouteAllSectionsAndDetail404(t *testing.T) {
	for _, failure := range []string{"", "404", "network"} {
		t.Run(failure, func(t *testing.T) {
			sections := map[string]bool{}
			requester := festivalRequester(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
				if input.Service != "cxcy" || input.Mode != "webvpn" || input.Method != "GET" || !input.ReadOnly {
					t.Fatal(input)
				}
				if strings.Contains(input.Path, "/comp/ucenter/") {
					section := strings.Split(input.Path, "/")[1]
					sections[section] = true
					if section == "popscience" {
						return []byte(`<div class="list_item">活动名称：测试活动<a href="../../front/comp/info?id=1">team</a></div>`), nil
					}
					return []byte(`<html><body>暂无记录</body></html>`), nil
				}
				if failure == "404" {
					return nil, &campus.Error{Code: "UPSTREAM_NOT_FOUND", Message: "synthetic"}
				}
				if failure == "network" {
					return nil, errors.New("synthetic connection error")
				}
				return []byte(`<h1>测试活动</h1><div>活动时间：2025-04-20</div>`), nil
			})
			result, err := FetchFestivalActivities(context.Background(), requester, "webvpn")
			if len(sections) != 4 {
				t.Fatal("missing section", sections)
			}
			if failure == "network" {
				if err == nil {
					t.Fatal("failed detail accepted")
				}
				return
			}
			if err != nil || len(result.Activities) != 1 {
				t.Fatal(result, err)
			}
			if failure == "404" && len(result.Warnings) != 1 {
				t.Fatal("missing warning")
			}
		})
	}
}

func TestFestivalProxyReferencesNormalizeToRegisteredOrigin(t *testing.T) {
	certificate, err := campus.WebVPNURL(festivalOrigin + "/static/uploads/res/popsciencecert/a.png?t=123")
	if err != nil {
		t.Fatal(err)
	}
	if value := FestivalCertificateURL(certificate, "popscience"); value != festivalOrigin+"/static/uploads/res/popsciencecert/a.png" {
		t.Fatal(value)
	}
	detail, err := campus.WebVPNURL(festivalOrigin + "/popscience/comp/front/comp/info?id=54")
	if err != nil {
		t.Fatal(err)
	}
	rows, _, err := ParseFestivalParticipation(`<div class="list_item"><a href="`+detail+`">team</a><img src="`+certificate+`"></div>`, "popscience")
	if err != nil || len(rows) != 1 || rows[0].DetailURL != festivalOrigin+"/popscience/comp/front/comp/info?id=54" || !rows[0].CertificateAvailable {
		t.Fatal(rows, err)
	}
}

func TestFestivalFetchRejectsLoginAndExcessivePages(t *testing.T) {
	for _, login := range []bool{false, true} {
		calls := 0
		requester := festivalRequester(func(context.Context, campus.ServiceRequest) ([]byte, error) {
			calls++
			if login {
				return []byte(`<html>统一身份认证<input name="password"></html>`), nil
			}
			return []byte(fmt.Sprintf(`<a href="?page=%d">下一页</a>`, calls+1)), nil
		})
		if _, err := FetchFestivalActivities(context.Background(), requester, "direct"); err == nil {
			t.Fatal("invalid scan accepted")
		}
		if login && calls != 1 || !login && calls != 50 {
			t.Fatal("unbounded reads", calls)
		}
	}
}
