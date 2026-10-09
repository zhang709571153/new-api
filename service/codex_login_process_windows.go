package service

import (
	"os/exec"
	"syscall"
)

func configureCodexLoginProcess(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
}
