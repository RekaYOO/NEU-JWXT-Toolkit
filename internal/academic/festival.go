package academic

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"golang.org/x/net/html"
)

const festivalOrigin = "https://cxcy.neu.edu.cn"

var festivalSections = []struct{ Key, Name string }{{"originality", "创意节"}, {"popscience", "科普节"}, {"technical", "科技节"}, {"business", "创业节"}}
var festivalLabels = regexp.MustCompile(`([\p{Han}]{2,8})\s*[：:]\s*`)
var festivalQuoted = regexp.MustCompile(`["']([^"']*/static/uploads/res/[^"']+)["']`)
var festivalSharedCertificate = regexp.MustCompile(`(?i)^/static/uploads/res/certificate/[A-Za-z0-9_-]{1,128}/[^/]+\.(png|jpe?g|webp)$`)
var festivalImageExtension = regexp.MustCompile(`(?i)\.(png|jpe?g|webp)$`)
var festivalTimestamp = regexp.MustCompile(`^\d{1,20}$`)
var festivalDate = regexp.MustCompile(`活动时间\s*[：:]\s*(\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)`)
var festivalDuration = regexp.MustCompile(`活动时间[^（(]*[（(]\s*([^（）()]*)\s*小时\s*[）)]`)

type FestivalActivity struct {
	ID                   string  `json:"id"`
	Section              string  `json:"section"`
	Name                 string  `json:"name"`
	DetailURL            string  `json:"detail_url"`
	Team                 string  `json:"team_name"`
	Status               string  `json:"status"`
	Category             string  `json:"category"`
	Type                 string  `json:"type"`
	Award                string  `json:"award"`
	SignIn               string  `json:"sign_in"`
	SignOut              string  `json:"sign_out"`
	CertificateAvailable bool    `json:"certificate_available"`
	CertificateURL       string  `json:"certificate_url,omitempty"`
	RegistrationTime     string  `json:"registration_time"`
	ActivityTime         string  `json:"activity_time"`
	StartTime            *string `json:"start_time"`
	Duration             string  `json:"duration"`
	Department           string  `json:"department"`
	Location             string  `json:"location"`
	Notes                string  `json:"notes"`
	Description          string  `json:"description"`
}

type FestivalSnapshot struct {
	Activities []FestivalActivity `json:"activities"`
	Warnings   []string           `json:"warnings"`
}

func festivalAttr(node *html.Node, key string) string {
	for _, attribute := range node.Attr {
		if attribute.Key == key {
			return attribute.Val
		}
	}
	return ""
}

func festivalWalk(node *html.Node, visit func(*html.Node)) {
	visit(node)
	for child := node.FirstChild; child != nil; child = child.NextSibling {
		festivalWalk(child, visit)
	}
}

func festivalText(node *html.Node) string {
	var values []string
	var walk func(*html.Node)
	walk = func(current *html.Node) {
		if current.Type == html.ElementNode && (current.Data == "script" || current.Data == "style" || current.Data == "noscript") {
			return
		}
		if current.Type == html.TextNode {
			values = append(values, current.Data)
		}
		for child := current.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(node)
	return strings.Join(strings.Fields(strings.Join(values, " ")), " ")
}

func festivalClass(node *html.Node, name string) bool {
	for _, item := range strings.Fields(festivalAttr(node, "class")) {
		if item == name {
			return true
		}
	}
	return false
}

func festivalLabel(text string, names ...string) string {
	labels := festivalLabels.FindAllStringSubmatchIndex(text, -1)
	for _, name := range names {
		for index, label := range labels {
			if text[label[2]:label[3]] != name {
				continue
			}
			end := len(text)
			if index+1 < len(labels) {
				end = labels[index+1][0]
			}
			return strings.TrimSpace(text[label[1]:end])
		}
	}
	return ""
}

func festivalURL(raw, base string, upgrade bool) string {
	reference, err := url.Parse(raw)
	if err != nil {
		return ""
	}
	if upgrade && reference.Scheme == "http" && reference.Hostname() == "cxcy.neu.edu.cn" && reference.User == nil && (reference.Port() == "" || reference.Port() == "80") {
		reference.Scheme, reference.Host = "https", "cxcy.neu.edu.cn"
	}
	baseURL, _ := url.Parse(base)
	absolute := baseURL.ResolveReference(reference)
	restored, err := campus.RestoreServiceURL(absolute.String(), festivalOrigin)
	if err != nil {
		return ""
	}
	parsed, err := url.Parse(restored)
	if err != nil {
		return ""
	}
	parsed.Fragment = ""
	return parsed.String()
}

func FestivalCertificateURL(raw, section string) string {
	valid := false
	for _, item := range festivalSections {
		if section == item.Key {
			valid = true
		}
	}
	if !valid || raw == "" {
		return ""
	}
	reference, err := url.Parse(raw)
	if err != nil || strings.ContainsAny(reference.Path, "\\%") {
		return ""
	}
	for _, segment := range strings.Split(reference.Path, "/") {
		if segment == "." || segment == ".." {
			return ""
		}
	}
	canonical := festivalURL(raw, festivalOrigin, false)
	if canonical == "" {
		return ""
	}
	parsed, _ := url.Parse(canonical)
	for _, character := range parsed.Path {
		if unicode.IsControl(character) {
			return ""
		}
	}
	if !festivalImageExtension.MatchString(parsed.Path) || (!strings.HasPrefix(parsed.Path, "/static/uploads/res/"+section+"cert/") && !festivalSharedCertificate.MatchString(parsed.Path)) {
		return ""
	}
	query, err := url.ParseQuery(parsed.RawQuery)
	if err != nil {
		return ""
	}
	for key, values := range query {
		key = strings.ToLower(key)
		if key != "t" && key != "ts" && key != "timestamp" && key != "_" {
			return ""
		}
		for _, value := range values {
			if !festivalTimestamp.MatchString(value) {
				return ""
			}
		}
	}
	parsed.RawQuery = ""
	return parsed.String()
}

func festivalDetailLink(node *html.Node, section string) string {
	if node.Type != html.ElementNode || node.Data != "a" {
		return ""
	}
	target := festivalURL(festivalAttr(node, "href"), festivalOrigin+"/"+section+"/comp/ucenter/main/index", true)
	parsed, err := url.Parse(target)
	if err != nil || parsed.Path != "/"+section+"/comp/front/comp/info" || !ValidEvaluationID(parsed.Query().Get("id")) {
		return ""
	}
	query := parsed.Query()
	for _, key := range []string{"t", "ts", "timestamp", "_"} {
		query.Del(key)
	}
	parsed.RawQuery = query.Encode()
	return parsed.String()
}

func festivalContainer(link *html.Node, section string) *html.Node {
	var fallback *html.Node
	for parent := link.Parent; parent != nil; parent = parent.Parent {
		if parent.Data != "div" && parent.Data != "tr" && parent.Data != "li" && parent.Data != "article" {
			continue
		}
		count := 0
		festivalWalk(parent, func(node *html.Node) {
			if festivalDetailLink(node, section) != "" {
				count++
			}
		})
		if count > 1 {
			break
		}
		if count != 1 {
			continue
		}
		if parent.Data != "div" || festivalClass(parent, "list_item") {
			return parent
		}
		if fallback == nil {
			fallback = parent
		}
	}
	if fallback != nil {
		return fallback
	}
	return link
}

func ParseFestivalParticipation(body, section string) ([]FestivalActivity, []string, error) {
	sectionName := ""
	for _, item := range festivalSections {
		if section == item.Key {
			sectionName = item.Name
		}
	}
	if sectionName == "" {
		return nil, nil, errors.New("四节活动分区无效")
	}
	root, err := html.Parse(strings.NewReader(body))
	if err != nil {
		return nil, nil, err
	}
	rows := []FestivalActivity{}
	seen, pages := map[string]bool{}, map[string]bool{}
	festivalWalk(root, func(link *html.Node) {
		if link.Data != "a" || festivalAttr(link, "href") == "" {
			return
		}
		target := festivalDetailLink(link, section)
		if target != "" {
			parsed, _ := url.Parse(target)
			identifier := parsed.Query().Get("id")
			if seen[identifier] {
				return
			}
			seen[identifier] = true
			container := festivalContainer(link, section)
			text := festivalText(container)
			team := festivalAttr(link, "title")
			if team == "" {
				team = festivalText(link)
			}
			name := festivalLabel(text, "活动名称", "名称")
			if name == "" {
				name = strings.TrimSpace(strings.Split(text, "类别")[0])
				if name == team {
					name = ""
				}
			}
			if name == "" {
				name = "活动 " + identifier
			}
			certificate := ""
			festivalWalk(container, func(node *html.Node) {
				if certificate != "" {
					return
				}
				for _, attribute := range []string{"href", "src", "data-url", "data-src"} {
					if value := FestivalCertificateURL(festivalAttr(node, attribute), section); value != "" {
						certificate = value
						return
					}
				}
				for _, match := range festivalQuoted.FindAllStringSubmatch(festivalAttr(node, "onclick"), -1) {
					if value := FestivalCertificateURL(match[1], section); value != "" {
						certificate = value
						return
					}
				}
			})
			if value := festivalLabel(text, "队伍名称", "团队名称", "队伍名"); value != "" {
				team = value
			}
			short := func(names ...string) string {
				values := strings.Fields(festivalLabel(text, names...))
				if len(values) == 0 {
					return ""
				}
				return values[0]
			}
			rows = append(rows, FestivalActivity{ID: identifier, Section: sectionName, Name: name, DetailURL: target, Team: team, Status: festivalLabel(text, "活动状态", "状态"), Category: festivalLabel(text, "类别"), Type: festivalLabel(text, "类型"), Award: festivalLabel(text, "获奖名次", "获奖", "奖项"), SignIn: short("签到情况", "签到状态", "签到"), SignOut: short("签退情况", "签退状态", "签退"), CertificateAvailable: certificate != "", CertificateURL: certificate})
			return
		}
		page := festivalURL(festivalAttr(link, "href"), festivalOrigin+"/"+section+"/comp/ucenter/main/index", true)
		parsed, err := url.Parse(page)
		if err != nil || !strings.HasPrefix(parsed.Path, "/"+section+"/comp/ucenter/") {
			return
		}
		for key := range parsed.Query() {
			if strings.EqualFold(key, "page") || strings.EqualFold(key, "p") {
				pages[parsed.RequestURI()] = true
			}
		}
	})
	pageList := []string{}
	for page := range pages {
		pageList = append(pageList, page)
	}
	sort.Strings(pageList)
	return rows, pageList, nil
}

func ParseFestivalDetail(body string, activity FestivalActivity) (FestivalActivity, error) {
	root, err := html.Parse(strings.NewReader(body))
	if err != nil {
		return activity, err
	}
	var title *html.Node
	priority := 0
	festivalWalk(root, func(node *html.Node) {
		rank := 0
		if node.Data == "h1" {
			rank = 1
		}
		if festivalClass(node, "title") && node.Parent != nil && festivalClass(node.Parent, "body_part1") {
			rank = 2
			for parent := node.Parent.Parent; parent != nil; parent = parent.Parent {
				if festivalClass(parent, "body_box") {
					rank = 3
					break
				}
			}
		}
		if rank > priority {
			priority, title = rank, node
		}
		if festivalClass(node, "body_part2") {
			heading, content := "", ""
			festivalWalk(node, func(child *html.Node) {
				if festivalClass(child, "p_title") {
					heading = festivalText(child)
				}
				if festivalClass(child, "content") {
					content = festivalText(child)
				}
			})
			if strings.Contains(heading, "注意事项") {
				activity.Notes = content
			}
			if strings.Contains(heading, "活动简介") {
				activity.Description = content
			}
		}
	})
	if title != nil {
		activity.Name = festivalText(title)
	}
	text := festivalText(root)
	if value := festivalLabel(text, "类别"); value != "" {
		activity.Category = value
	}
	if value := festivalLabel(text, "类型"); value != "" {
		activity.Type = value
	}
	activity.RegistrationTime, activity.ActivityTime = festivalLabel(text, "报名时间"), festivalLabel(text, "活动时间")
	activity.Department, activity.Location = festivalLabel(text, "所属部门"), festivalLabel(text, "活动地点")
	if match := festivalDate.FindStringSubmatch(text); len(match) > 1 {
		for _, layout := range []string{"2006-1-2 15:04:05", "2006-1-2 15:04", "2006-1-2"} {
			if parsed, err := time.Parse(layout, match[1]); err == nil {
				value := parsed.Format("2006-01-02T15:04:05")
				activity.StartTime = &value
				break
			}
		}
	}
	if match := festivalDuration.FindStringSubmatch(text); len(match) > 1 {
		activity.Duration = strings.TrimSpace(match[1])
	}
	return activity, nil
}

func FetchFestivalActivities(ctx context.Context, requester ServiceRequester, mode string) (FestivalSnapshot, error) {
	result := FestivalSnapshot{Activities: []FestivalActivity{}, Warnings: []string{}}
	positions := map[string]int{}
	read := func(path string) (string, error) {
		body, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "cxcy", Mode: mode, Method: http.MethodGet, Path: path, ReadOnly: true})
		if err != nil {
			return "", err
		}
		text := string(body)
		if strings.Contains(text, "统一身份认证") && strings.Contains(strings.ToLower(text), "password") {
			return "", &campus.Error{Code: "CXCY_LOGIN_REQUIRED", Message: "创院系统登录已失效"}
		}
		return text, nil
	}
	for _, section := range festivalSections {
		pending := []string{"/" + section.Key + "/comp/ucenter/main/index"}
		visited := map[string]bool{}
		for len(pending) > 0 {
			path := pending[0]
			pending = pending[1:]
			if visited[path] {
				continue
			}
			if len(visited) >= 50 {
				return FestivalSnapshot{}, errors.New("四节活动分页超过安全限制")
			}
			visited[path] = true
			body, err := read(path)
			if err != nil {
				return FestivalSnapshot{}, err
			}
			rows, pages, err := ParseFestivalParticipation(body, section.Key)
			if err != nil {
				return FestivalSnapshot{}, err
			}
			pending = append(pending, pages...)
			for _, row := range rows {
				key := section.Key + ":" + row.ID
				if index, exists := positions[key]; exists {
					result.Activities[index] = row
				} else {
					positions[key] = len(result.Activities)
					result.Activities = append(result.Activities, row)
				}
				if len(result.Activities) > 500 {
					return FestivalSnapshot{}, errors.New("四节活动数量超过安全限制")
				}
			}
		}
	}
	for index, activity := range result.Activities {
		parsed, _ := url.Parse(activity.DetailURL)
		body, err := read(parsed.RequestURI())
		if err != nil {
			var failure *campus.Error
			if errors.As(err, &failure) && failure.Code == "UPSTREAM_NOT_FOUND" {
				result.Warnings = append(result.Warnings, activity.Section+"「"+activity.Name+"」详情不存在")
				continue
			}
			return FestivalSnapshot{}, err
		}
		updated, err := ParseFestivalDetail(body, activity)
		if err != nil {
			return FestivalSnapshot{}, err
		}
		result.Activities[index] = updated
		if updated.StartTime == nil {
			result.Warnings = append(result.Warnings, updated.Section+"「"+updated.Name+"」缺少活动时间")
		}
	}
	return result, nil
}
