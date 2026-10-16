# Chromium sandbox syscall profile

Unmodified official Microsoft Playwright profile, revision `ae935a43d9e376e4759548f6b3c6905c7b282333`.

Source: [https://raw.githubusercontent.com/microsoft/playwright/ae935a43d9e376e4759548f6b3c6905c7b282333/utils/docker/seccomp_profile.json](https://raw.githubusercontent.com/microsoft/playwright/ae935a43d9e376e4759548f6b3c6905c7b282333/utils/docker/seccomp_profile.json)

SHA-256: `cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`

The source is distributed under the [Apache License 2.0](https://github.com/microsoft/playwright/blob/ae935a43d9e376e4759548f6b3c6905c7b282333/LICENSE), included in `seccomp-profile.LICENSE`. It retains the Docker default deny policy and permits the user namespace syscalls required by Chromium. Follow [Playwright Docker guidance](https://playwright.dev/python/docs/docker) and keep API/Worker non-root. No `seccomp=unconfined`, host IPC or `SYS_ADMIN` capability is enabled.
