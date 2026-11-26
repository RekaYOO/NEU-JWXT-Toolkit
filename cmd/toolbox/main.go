package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/application"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/processlock"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/web"
)

var errUpgradeRestored = errors.New("升级启动失败，已恢复升级前数据库")

const upgradeRestoredExit = 78

func run() (result error) {
	if len(os.Args) == 2 && os.Args[1] == "linux-upgrade-protocol" && runtime.GOOS == "linux" {
		fmt.Println("1")
		return nil
	}
	if len(os.Args) > 1 && os.Args[1] == "check-upgrade" {
		if len(os.Args) != 3 || os.Args[2] == "" {
			return errors.New("check-upgrade requires an existing data directory")
		}
		if err := store.CheckUpgrade(context.Background(), os.Args[2]); err != nil {
			return err
		}
		fmt.Println("toolbox-upgrade-check:ok")
		return nil
	}
	if len(os.Args) > 1 && (os.Args[1] == "install-update" || os.Args[1] == "launch" || os.Args[1] == "rollback-update") {
		return desktopReleaseCommand(os.Args[1], os.Args[2:])
	}
	if len(os.Args) > 1 && os.Args[1] == "stage-update" {
		return stageWindowsUpdate(os.Args[2:], os.Stdout)
	}
	if len(os.Args) > 1 && os.Args[1] == "import-legacy" {
		return runLegacyImport(context.Background(), os.Args[2:], os.Stdout)
	}
	if len(os.Args) > 1 && (os.Args[1] == "backup" || os.Args[1] == "restore") {
		return runDataCommand(context.Background(), os.Args[1], os.Args[2:], os.Stdout)
	}
	executable, _ := os.Executable()
	workingDirectory, err := os.Getwd()
	if err != nil {
		return err
	}
	configDirectory, _ := os.UserConfigDir()
	if local := os.Getenv("LOCALAPPDATA"); local != "" {
		configDirectory = local
	}
	paths := resolveRuntimePaths(executable, workingDirectory, configDirectory)
	listen := flag.String("listen", "127.0.0.1:8000", "HTTP listening address")
	assets := flag.String("assets", paths.Assets, "production frontend directory")
	data := flag.String("data", paths.Data, "account data directory")
	showVersion := flag.Bool("version", false, "print version and exit without opening data")
	defaultMode := os.Getenv("NEU_JWXT_MODE")
	if defaultMode == "" {
		defaultMode = "local-single"
	}
	mode := flag.String("mode", defaultMode, "local-single, remote-single, or remote-multi")
	tray := flag.Bool("tray", paths.Bundled && runtime.GOOS == "windows" && defaultMode == "local-single", "show Windows desktop tray")
	openBrowser := flag.Bool("open-browser", paths.Bundled && runtime.GOOS == "windows" && defaultMode == "local-single", "open the desktop URL in the default browser")
	origin := flag.String("public-origin", os.Getenv("NEU_JWXT_PUBLIC_ORIGIN"), "external HTTPS origin for remote deployment")
	container := flag.Bool("container", false, "allow an unspecified listening IP inside a remote container")
	proxies := flag.String("trusted-proxies", "127.0.0.1/32,::1/128", "comma-separated reverse proxy IP prefixes")
	initialize := flag.Bool("init-only", false, "initialize deployment credentials without starting HTTP")
	reset := flag.String("reset-site-password", "", "replace access or admin password from its environment variable")
	flag.Parse()
	if *showVersion {
		payload, err := os.ReadFile(paths.Version)
		if err != nil {
			return err
		}
		fmt.Println(strings.TrimSpace(string(payload)))
		return nil
	}
	if override := os.Getenv("NEU_JWXT_DATA_DIR"); override != "" {
		*data = override
	}
	linuxGate := ""
	if runtime.GOOS == "linux" && paths.Bundled && *mode != "local-single" && !*initialize && *reset == "" {
		marker := filepath.Join(filepath.Dir(executable), ".upgrade-pending")
		if _, err := os.Lstat(marker); err == nil {
			linuxGate = marker
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	if err := validateDeployment(*listen, *mode, *origin, *container); err != nil {
		return err
	}
	if *mode != "local-single" {
		*tray = false
		*openBrowser = false
	}
	instanceLock, err := processlock.Acquire(*data)
	if err != nil {
		if errors.Is(err, processlock.ErrInUse) && *mode == "local-single" && *openBrowser && !*initialize && *reset == "" {
			if endpoint, readErr := existingDesktop(*data); readErr == nil {
				return openDesktopBrowser(endpoint)
			}
		}
		return err
	}
	defer instanceLock.Close()
	if runtime.GOOS == "windows" && paths.Bundled && *mode == "local-single" && !*initialize && *reset == "" {
		if err := registerUpgradeCheckpoint(context.Background(), *data, executable); err != nil {
			return fmt.Errorf("prepare upgrade checkpoint: %w", err)
		}
		if slotName.MatchString(filepath.Base(filepath.Dir(executable))) {
			defer func() {
				if result == nil {
					return
				}
				if _, err := os.Lstat(checkpointMarker(*data, executable)); errors.Is(err, os.ErrNotExist) {
					return
				}
				if err := restoreLockedUpgradeCheckpoint(context.Background(), *data, executable); err != nil {
					result = errors.Join(result, fmt.Errorf("upgrade rollback: %w", err))
				} else {
					result = errors.Join(result, errUpgradeRestored)
				}
			}()
		}
	}
	database, err := store.Open(*data)
	if err != nil {
		return err
	}
	defer database.Close()
	accounts, err := application.NewAccounts(database, *mode)
	if err != nil {
		return err
	}
	defer accounts.Close()
	if *reset != "" {
		password := ""
		switch *reset {
		case "access":
			password = os.Getenv("NEU_JWXT_ACCESS_PASSWORD")
		case "admin":
			password = os.Getenv("NEU_JWXT_ADMIN_PASSWORD")
		default:
			return fmt.Errorf("reset-site-password must be access or admin")
		}
		return database.SetSitePassword(context.Background(), *reset, password, true)
	}
	version := "initializing"
	if !*initialize {
		payload, err := os.ReadFile(paths.Version)
		if err != nil {
			return err
		}
		version = strings.TrimSpace(string(payload))
	}
	api := application.NewInactiveAPI(database, accounts, version)
	if runtime.GOOS == "windows" && paths.Bundled && *mode == "local-single" {
		if _, err := desktopInstallRoot(executable); err == nil {
			api.SetDesktopUpdateInstaller(func(archive, digest string) error {
				return runDesktopReleaseCommand("install-update", []string{"-archive", archive, "-sha256", digest, "-check-data", *data}, true)
			})
		}
		api.SetUpdatePreparer(func(archive, digest, parent string) (string, error) {
			var prepared string
			err := prepareWindowsUpdate(archive, digest, parent, func(stage string) error { prepared = stage; return nil })
			return prepared, err
		})
	}
	if err := api.ConfigureUpdateDirectory(filepath.Join(*data, "updates")); err != nil {
		return err
	}
	if err := api.SetTrustedProxies(*proxies); err != nil {
		return err
	}
	api.SecureCookies = *mode != "local-single"
	if err := api.ConfigureRemoteAccess(context.Background(), os.Getenv("NEU_JWXT_ACCESS_PASSWORD"), os.Getenv("NEU_JWXT_ADMIN_PASSWORD")); err != nil {
		return err
	}
	if err := api.ConfigureDefaultAdministrator(context.Background(), os.Getenv("NEU_JWXT_ADMIN_ACCOUNT")); err != nil {
		return err
	}
	if *initialize {
		return nil
	}
	static, err := web.NewStatic(*assets)
	if err != nil {
		return fmt.Errorf("build frontend before starting: %w", err)
	}
	defer static.Close()
	upgrading := runtime.GOOS == "windows" && paths.Bundled && *mode == "local-single" && slotName.MatchString(filepath.Base(filepath.Dir(executable)))
	if !upgrading {
		if err := accounts.ResumeSaved(); err != nil {
			return err
		}
		if linuxGate == "" {
			api.StartBackgroundWork()
		}
	}
	router := http.NewServeMux()
	api.Register(router)
	router.Handle("/", static)
	var ready atomic.Bool
	var handler http.Handler = router
	if linuxGate != "" {
		handler = linuxStartupGate(router, &ready)
	}
	server := &http.Server{Addr: *listen, Handler: api.Boundary(handler), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 120 * time.Second, IdleTimeout: 90 * time.Second, MaxHeaderBytes: 1 << 20}
	if upgrading {
		server.Handler = upgradeStartupGate(server.Handler, &ready)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	listener, err := net.Listen("tcp", *listen)
	if err != nil {
		return err
	}
	defer listener.Close()
	if *mode == "local-single" {
		if err := api.ConfigureDesktopShutdown(listener.Addr().String(), stop); err != nil {
			return err
		}
		if err := saveDesktopInstance(*data, "http://"+listener.Addr().String()); err != nil {
			return err
		}
		defer os.Remove(filepath.Join(*data, "desktop-instance.json"))
	}
	finished := make(chan error, 1)
	go func() { finished <- server.Serve(listener) }()
	if upgrading {
		if err := confirmUpgradeCheckpoint(*data, executable, "http://"+listener.Addr().String(), version, accounts.ResumeSaved); err != nil {
			closeErr := server.Close()
			<-finished
			return errors.Join(fmt.Errorf("upgrade checkpoint confirmation: %w", err), closeErr)
		}
		api.StartBackgroundWork()
		ready.Store(true)
	}
	if linuxGate != "" {
		activated, err := waitForLinuxActivation(ctx, finished, linuxGate)
		if err != nil {
			return errors.Join(err, server.Close())
		}
		if activated {
			api.StartBackgroundWork()
			ready.Store(true)
		}
	}
	log.Printf("Toolbox Go migration runtime: http://%s", listener.Addr().String())
	waitErr := waitDesktop(ctx, finished, stop, "http://"+listener.Addr().String(), paths.Icon, *tray, *openBrowser)
	stop()
	shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	shutdownErr := server.Shutdown(shutdown)
	if waitErr != nil {
		return waitErr
	}
	return shutdownErr
}

func validateDeployment(listen, mode, origin string, container bool) error {
	if mode != "local-single" && mode != "remote-single" && mode != "remote-multi" {
		return fmt.Errorf("unknown deployment mode")
	}
	host, _, err := net.SplitHostPort(listen)
	address := net.ParseIP(host)
	if err != nil || address == nil || (!address.IsLoopback() && !(container && mode != "local-single" && address.IsUnspecified())) {
		return fmt.Errorf("listen must use a loopback IP; remote containers may explicitly enable --container")
	}
	if mode != "local-single" {
		parsed, err := url.Parse(origin)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || (parsed.Path != "" && parsed.Path != "/") || parsed.RawQuery != "" || parsed.Fragment != "" {
			return fmt.Errorf("remote mode requires --public-origin with an HTTPS origin and no path")
		}
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		if errors.Is(err, errUpgradeRestored) {
			log.Print(err)
			os.Exit(upgradeRestoredExit)
		}
		log.Fatal(err)
	}
}
