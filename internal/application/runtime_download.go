package application

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type runtimeUpdateJob struct {
	PreparedPath string               `json:"prepared_path,omitempty"`
	ID           string               `json:"job_id"`
	State        string               `json:"state"`
	Message      string               `json:"message"`
	Total        int64                `json:"bytes_total"`
	Downloaded   int64                `json:"bytes_downloaded"`
	Path         string               `json:"path,omitempty"`
	Error        string               `json:"error,omitempty"`
	SHA256       string               `json:"sha256,omitempty"`
	Updated      string               `json:"updated_at,omitempty"`
	History      []runtimeUpdateEvent `json:"history,omitempty"`
}

type runtimeUpdateEvent struct {
	State   string `json:"state"`
	At      string `json:"at"`
	Message string `json:"message"`
}

func (api *API) SetUpdatePreparer(prepare func(string, string, string) (string, error)) {
	api.releaseChecker.mutex.Lock()
	defer api.releaseChecker.mutex.Unlock()
	api.releaseChecker.prepare = prepare
}

func (api *API) SetDesktopUpdateInstaller(install func(string, string) error) {
	api.releaseChecker.mutex.Lock()
	defer api.releaseChecker.mutex.Unlock()
	api.releaseChecker.install = install
}

func (api *API) installDesktopUpdate(response http.ResponseWriter, request *http.Request) {
	if !api.authorizeDesktopAction(response, request) {
		return
	}
	checker := &api.releaseChecker
	checker.mutex.Lock()
	install, job := checker.install, checker.job
	if install != nil && job != nil && job.ID == request.PathValue("job_id") && job.State == "selected" {
		checker.mutex.Unlock()
		writeJSON(response, 200, map[string]any{"success": true, "restart_required": true})
		return
	}
	if install == nil || checker.installBusy || job == nil || job.State != "downloaded" || job.ID != request.PathValue("job_id") {
		checker.mutex.Unlock()
		writeJSON(response, 409, map[string]string{"detail": "当前没有可安装的下载任务，或安装正在进行"})
		return
	}
	archive, digest := job.Path, job.SHA256
	previous := *job
	job.State, job.Message = "installing", "正在校验并安装新版程序"
	if err := api.persistUpdateJobLocked(); err != nil {
		*job = previous
		checker.mutex.Unlock()
		writeJSON(response, 409, map[string]string{"detail": "无法记录安装任务，未开始安装"})
		return
	}
	checker.installBusy = true
	checker.mutex.Unlock()
	installErr := install(archive, digest)
	checker.mutex.Lock()
	defer checker.mutex.Unlock()
	checker.installBusy = false
	job.State, job.Message = "selected", "已为下次启动选定新版程序"
	if installErr != nil {
		job.State, job.Message = "downloaded", "安装未完成，已保留下载包"
	}
	if err := api.persistUpdateJobLocked(); err != nil {
		job.State, job.Message = "install_unknown", "无法保存安装结果，请核对当前启动版本"
		writeJSON(response, 503, map[string]string{"detail": job.Message})
		return
	}
	if installErr != nil {
		writeJSON(response, 409, map[string]string{"detail": "安装校验或新版启动检查失败，原启动版本未改变"})
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "restart_required": true})
}

func (api *API) ConfigureUpdateDirectory(directory string) error {
	absolute, err := filepath.Abs(directory)
	if err != nil {
		return err
	}
	api.releaseChecker.mutex.Lock()
	defer api.releaseChecker.mutex.Unlock()
	api.releaseChecker.directory = absolute
	payload, err := api.database.SiteSetting(context.Background(), "runtime-update-job")
	if errors.Is(err, store.ErrMissing) {
		return nil
	}
	if err != nil {
		return err
	}
	var job runtimeUpdateJob
	if len(payload) > 64*1024 || json.Unmarshal(payload, &job) != nil || len(job.ID) != 32 {
		return errors.New("stored update job is invalid")
	}
	if _, err := hex.DecodeString(job.ID); err != nil {
		return errors.New("stored update job is invalid")
	}
	api.releaseChecker.jobSaved = payload
	api.releaseChecker.job = &job
	switch job.State {
	case "installing":
		job.State, job.Message = "install_unknown", "上次安装结果尚未确认，请核对启动版本；未重复安装"
	case "selected", "install_unknown":
	case "downloading", "verifying":
		if err := cleanInterruptedDownload(absolute, job.ID); err != nil {
			return err
		}
		job.State, job.Message, job.Error = "failed", "上次更新下载已中断", "程序重启前未完成校验；请重新检查版本并下载"
		job.Path = ""
	case "downloaded":
		if !verifySavedRelease(absolute, job) {
			job.State, job.Message, job.Error = "failed", "已下载文件不可用", "文件缺失或校验不符，请重新下载"
			job.Path = ""
		} else if job.PreparedPath != "" && !hasPreparedLayout(absolute, job) {
			job.PreparedPath = ""
			job.Message = "暂存目录不可用；已下载的更新包仍可手动安装"
		}
	case "failed":
	default:
		return errors.New("stored update state is invalid")
	}
	if job.State == "failed" {
		job.PreparedPath = ""
	}
	return api.persistUpdateJobLocked()
}

func hasPreparedLayout(directory string, job runtimeUpdateJob) bool {
	parent := filepath.Join(directory, "download-"+job.ID)
	name := filepath.Base(job.PreparedPath)
	if job.PreparedPath != filepath.Join(parent, name) || filepath.Dir(job.Path) != parent || !strings.HasPrefix(name, ".toolbox-update-") {
		return false
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return false
	}
	defer root.Close()
	for _, component := range []string{"download-" + job.ID, name, "frontend", "build"} {
		info, err := root.Lstat(component)
		if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return false
		}
		child, err := root.OpenRoot(component)
		if err != nil {
			return false
		}
		defer child.Close()
		root = child
		files := []string(nil)
		if component == name {
			files = []string{"VERSION", "toolbox.exe"}
		} else if component == "build" {
			files = []string{"index.html"}
		}
		for _, file := range files {
			info, err := root.Lstat(file)
			if err != nil || !info.Mode().IsRegular() || info.Size() == 0 {
				return false
			}
		}
	}
	return true
}

func cleanInterruptedDownload(directory, id string) error {
	if len(id) != 32 {
		return errors.New("invalid download identifier")
	}
	if _, err := hex.DecodeString(id); err != nil {
		return err
	}
	base, err := os.OpenRoot(directory)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	defer base.Close()
	stage := "download-" + id
	info, err := base.Lstat(stage)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil
	}
	root, err := base.OpenRoot(stage)
	if err != nil {
		return err
	}
	defer root.Close()
	partial, err := root.Lstat("payload.partial")
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if !partial.Mode().IsRegular() {
		return nil
	}
	return root.Remove("payload.partial")
}

func verifySavedRelease(directory string, job runtimeUpdateJob) bool {
	if job.Total < 1 || job.Total > 1024*1024*1024 || !releaseDigest.MatchString(job.SHA256) {
		return false
	}
	relative, err := filepath.Rel(directory, job.Path)
	if err != nil || filepath.IsAbs(relative) || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return false
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return false
	}
	defer root.Close()
	file, err := root.Open(relative)
	if err != nil {
		return false
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() != job.Total {
		return false
	}
	hash := sha256.New()
	count, err := io.Copy(hash, io.LimitReader(file, job.Total+1))
	return err == nil && count == job.Total && hex.EncodeToString(hash.Sum(nil)) == job.SHA256
}

func (api *API) persistUpdateJobLocked() error {
	checker := &api.releaseChecker
	checker.job.Updated = time.Now().UTC().Format(time.RFC3339Nano)
	history := checker.job.History
	if len(history) == 0 || history[len(history)-1].State != checker.job.State {
		checker.job.History = append(history, runtimeUpdateEvent{State: checker.job.State, At: checker.job.Updated, Message: checker.job.Message})
		if len(checker.job.History) > 16 {
			checker.job.History = checker.job.History[len(checker.job.History)-16:]
		}
	}
	payload, err := json.Marshal(checker.job)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := api.database.SaveUpdateJob(ctx, checker.jobSaved, payload); err != nil {
		return err
	}
	checker.jobSaved = payload
	return nil
}

func (api *API) mayManageUpdates(request *http.Request) bool {
	return api.accounts.mode == "local-single" || api.siteAuthenticated(request, "admin")
}

func (api *API) runtimeDownload(response http.ResponseWriter, request *http.Request) {
	if !api.mayManageUpdates(request) {
		writeJSON(response, 403, map[string]string{"detail": "服务端更新需要管理员身份"})
		return
	}
	checker := &api.releaseChecker
	checker.mutex.Lock()
	defer checker.mutex.Unlock()
	if checker.directory == "" || checker.latest == nil || checker.failure != nil || time.Since(checker.checked) > 10*time.Minute {
		writeJSON(response, 409, map[string]string{"detail": "请先检查并确认可用的 Go/Web 更新"})
		return
	}
	if checker.installBusy {
		writeJSON(response, 409, map[string]string{"detail": "正在安装更新"})
		return
	}
	if checker.job != nil && (checker.job.State == "downloading" || checker.job.State == "verifying") {
		writeJSON(response, 200, *checker.job)
		return
	}
	comparison, err := compareReleaseVersions(checker.latest.Version, api.version)
	asset, exists := checker.latest.Assets[runtime.GOOS+"-"+runtime.GOARCH]
	if err != nil || comparison <= 0 || !exists {
		writeJSON(response, 409, map[string]string{"detail": "没有适用当前平台的新版本"})
		return
	}
	identifier := make([]byte, 16)
	if _, err := rand.Read(identifier); err != nil {
		writeJSON(response, 500, map[string]string{"detail": "无法创建更新任务"})
		return
	}
	job := runtimeUpdateJob{ID: hex.EncodeToString(identifier), State: "downloading", Message: "正在下载更新包", Total: asset.Size, SHA256: asset.SHA256}
	previous := checker.job
	checker.job = &job
	if err := api.persistUpdateJobLocked(); err != nil {
		checker.job = previous
		writeJSON(response, 409, map[string]string{"detail": "更新任务无法保存，请检查是否有其他工具箱进程运行"})
		return
	}
	client := *checker.client
	client.Timeout = 10 * time.Minute
	client.CheckRedirect = func(next *http.Request, via []*http.Request) error {
		if len(via) >= 5 || !allowedReleaseRedirect(next.URL) {
			return errors.New("untrusted redirect")
		}
		return nil
	}
	go api.downloadRelease(api.accounts.context, &client, asset, job.ID, checker.directory)
	writeJSON(response, 200, job)
}

func (api *API) runtimeDownloadJob(response http.ResponseWriter, request *http.Request) {
	if !api.mayManageUpdates(request) {
		writeJSON(response, 403, map[string]string{"detail": "服务端更新需要管理员身份"})
		return
	}
	checker := &api.releaseChecker
	checker.mutex.Lock()
	defer checker.mutex.Unlock()
	if checker.job == nil || checker.job.ID != request.PathValue("job_id") {
		writeJSON(response, 404, map[string]string{"detail": "更新任务不存在"})
		return
	}
	writeJSON(response, 200, *checker.job)
}

func (api *API) changeUpdateJob(id string, change func(*runtimeUpdateJob)) {
	api.releaseChecker.mutex.Lock()
	defer api.releaseChecker.mutex.Unlock()
	if api.releaseChecker.job != nil && api.releaseChecker.job.ID == id {
		previousState := api.releaseChecker.job.State
		change(api.releaseChecker.job)
		if api.releaseChecker.job.State != previousState {
			if err := api.persistUpdateJobLocked(); err != nil {
				api.releaseChecker.job.State = "failed"
				api.releaseChecker.job.Message = "更新任务保存失败"
				api.releaseChecker.job.Error = "未安装任何内容；请重启后重新检查下载状态"
				api.releaseChecker.job.Path = ""
			}
		}
	}
}

func (api *API) downloadRelease(ctx context.Context, client *http.Client, asset runtimeReleaseAsset, id, directory string) {
	failure := func() {
		api.changeUpdateJob(id, func(job *runtimeUpdateJob) {
			job.State = "failed"
			job.Message = "更新下载或校验失败"
			job.Error = "未安装任何内容；请检查网络或重新检查发行版本后重试"
		})
	}
	parsed, err := url.Parse(asset.URL)
	if err != nil || !allowedReleaseRedirect(parsed) || filepath.Base(asset.Name) != asset.Name || asset.Size < 1 || asset.Size > 1024*1024*1024 {
		failure()
		return
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		failure()
		return
	}
	stage := filepath.Join(directory, "download-"+id)
	if err := os.Mkdir(stage, 0700); err != nil {
		failure()
		return
	}
	partial := filepath.Join(stage, "payload.partial")
	defer os.Remove(stage)
	defer os.Remove(partial)
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, asset.URL, nil)
	if err != nil {
		failure()
		return
	}
	request.Header.Set("User-Agent", "NEU-JWXT-Toolkit-Go-update-checker")
	response, err := client.Do(request)
	if err != nil {
		failure()
		return
	}
	defer response.Body.Close()
	if response.StatusCode != 200 || response.ContentLength > 0 && response.ContentLength != asset.Size {
		failure()
		return
	}
	file, err := os.OpenFile(partial, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		failure()
		return
	}
	hash := sha256.New()
	reader := io.LimitReader(response.Body, asset.Size+1)
	buffer := make([]byte, 64*1024)
	var total int64
	for {
		count, readErr := reader.Read(buffer)
		if count > 0 {
			total += int64(count)
			if total > asset.Size {
				file.Close()
				failure()
				return
			}
			if _, err := file.Write(buffer[:count]); err != nil {
				file.Close()
				failure()
				return
			}
			hash.Write(buffer[:count])
			api.changeUpdateJob(id, func(job *runtimeUpdateJob) { job.Downloaded = total })
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			file.Close()
			failure()
			return
		}
	}
	api.changeUpdateJob(id, func(job *runtimeUpdateJob) { job.State = "verifying"; job.Message = "正在校验更新包" })
	syncErr := file.Sync()
	closeErr := file.Close()
	if syncErr != nil || closeErr != nil || total != asset.Size || hex.EncodeToString(hash.Sum(nil)) != asset.SHA256 {
		failure()
		return
	}
	target := filepath.Join(stage, asset.Name)
	if err := os.Rename(partial, target); err != nil {
		failure()
		return
	}
	api.releaseChecker.mutex.Lock()
	prepare := api.releaseChecker.prepare
	api.releaseChecker.mutex.Unlock()
	var prepared string
	message := "更新包已校验，请备份并停止服务后替换程序目录"
	if prepare != nil {
		prepared, err = prepare(target, asset.SHA256, stage)
		if err != nil {
			prepared = ""
			message = "更新包已校验，但暂存失败；可使用已下载的压缩包手动安装"
		} else {
			message = "新版程序已暂存，尚未切换；请先备份数据"
		}
	}
	api.changeUpdateJob(id, func(job *runtimeUpdateJob) {
		job.State = "downloaded"
		job.Message = message
		job.PreparedPath = prepared
		job.Path = target
	})
}
