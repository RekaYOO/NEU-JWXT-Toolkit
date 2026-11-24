package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"unsafe"
)

func launch() error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	directory := filepath.Dir(self)
	command := exec.Command(filepath.Join(directory, "toolbox.exe"), append([]string{"launch"}, os.Args[1:]...)...)
	command.Dir = directory
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	return command.Run()
}

func main() {
	if err := launch(); err != nil {
		text, _ := syscall.UTF16PtrFromString("启动失败。请运行同目录的启动工具箱.cmd 查看详细信息。\n" + err.Error())
		title, _ := syscall.UTF16PtrFromString("NEU 教务系统工具箱")
		syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(text)), uintptr(unsafe.Pointer(title)), 0x10)
		os.Exit(1)
	}
}
