package main

import (
	"golang.org/x/sys/windows"
	"os"
	"time"
)

func lockFile(path string) (*os.File, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	err = windows.LockFileEx(windows.Handle(f.Fd()), windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, &windows.Overlapped{})
	if err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}

func openPrivateFile(path string) (*os.File, error) {
	for attempt := 0; ; attempt++ {
		f, err := os.Open(path)
		if err == nil || attempt >= 9 {
			return f, err
		}
		cause := err
		if pathErr, ok := err.(*os.PathError); ok {
			cause = pathErr.Err
		}
		if cause != windows.ERROR_SHARING_VIOLATION && cause != windows.ERROR_LOCK_VIOLATION {
			return nil, err
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func replaceFile(source, destination string) error {
	src, err := windows.UTF16PtrFromString(source)
	if err != nil {
		return err
	}
	dst, err := windows.UTF16PtrFromString(destination)
	if err != nil {
		return err
	}
	// A dashboard reader or virus scanner can briefly hold the previous file
	// without FILE_SHARE_DELETE. Keep the old version intact and retry a bounded
	// sharing violation; never remove it before the atomic replacement.
	for attempt := 0; ; attempt++ {
		err = windows.MoveFileEx(src, dst, windows.MOVEFILE_REPLACE_EXISTING|windows.MOVEFILE_WRITE_THROUGH)
		if err == nil || attempt >= 49 || (err != windows.ERROR_SHARING_VIOLATION && err != windows.ERROR_LOCK_VIOLATION && err != windows.ERROR_ACCESS_DENIED) {
			return err
		}
		time.Sleep(20 * time.Millisecond)
	}
}
