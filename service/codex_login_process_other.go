//go:build !windows

package service

import "os/exec"

func configureCodexLoginProcess(cmd *exec.Cmd) {}
