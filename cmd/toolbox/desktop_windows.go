package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"runtime"

	"fyne.io/systray"
	"golang.org/x/sys/windows"
)

func openDesktopBrowser(target string) error {
	operation, _ := windows.UTF16PtrFromString("open")
	address, err := windows.UTF16PtrFromString(target)
	if err != nil {
		return err
	}
	return windows.ShellExecute(0, operation, address, nil, nil, windows.SW_SHOWNORMAL)
}

func waitDesktop(ctx context.Context, finished <-chan error, stop context.CancelFunc, endpoint, icon string, tray, browser bool) error {
	if !tray {
		if browser {
			if err := openDesktopBrowser(endpoint); err != nil {
				log.Print("浏览器未能打开，请手动访问工具箱地址")
			}
		}
		select {
		case err := <-finished:
			return err
		case <-ctx.Done():
			return nil
		}
	}
	iconBytes, err := os.ReadFile(icon)
	if err != nil {
		return fmt.Errorf("desktop tray icon unavailable: %w", err)
	}
	result := make(chan error, 1)
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	systray.Run(func() {
		systray.SetIcon(iconBytes)
		systray.SetTooltip("NEU 教务工具箱")
		open := systray.AddMenuItem("打开工具箱", "在浏览器中打开工具箱")
		systray.AddSeparator()
		quit := systray.AddMenuItem("退出程序", "停止本地服务并退出")
		if browser {
			if err := openDesktopBrowser(endpoint); err != nil {
				log.Print("浏览器未能打开，请使用托盘菜单重试")
			}
		}
		go func() {
			for {
				select {
				case <-open.ClickedCh:
					if err := openDesktopBrowser(endpoint); err != nil {
						log.Print("浏览器未能打开")
					}
				case <-quit.ClickedCh:
					stop()
				case <-ctx.Done():
					result <- nil
					systray.Quit()
					return
				case err := <-finished:
					result <- err
					stop()
					systray.Quit()
					return
				}
			}
		}()
	}, func() { stop() })
	select {
	case err := <-result:
		return err
	default:
		return nil
	}
}
