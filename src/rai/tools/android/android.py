"""Android security tools — adb, apktool, jadx, frida, manifest audit."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from rai.tools.core.findings import _add_finding


def _run_cmd(cmd: list[str], timeout: int = 120) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as exc:
        return f"Error: {exc}"


# ── APK Info ───────────────────────────────────────────────────────────────


class APKInfoInput(BaseModel):
    apk_path: str = Field(description="Path to the APK file.")


class APKInfoTool(BaseTool):
    """Extract APK metadata: package name, permissions, min SDK, target SDK."""

    name: str = "apk_info"
    description: str = (
        "Extract APK metadata: package name, version, permissions, min/target SDK, "
        "and packer detection (apkid). "
        "Uses aapt2 or aapt. Install: Android SDK build-tools, pip install apkid"
    )
    args_schema: ClassVar[type[BaseModel]] = APKInfoInput

    def _run(self, apk_path: str) -> str:
        import shutil
        sections: list[str] = []

        aapt = shutil.which("aapt2") or shutil.which("aapt")
        if aapt:
            out = _run_cmd([aapt, "dump", "badging", apk_path])
            sections.append(f"[aapt badging]\n{out[:3000]}")
        else:
            sections.append("[aapt] not found — install Android SDK build-tools")

        if shutil.which("apkid"):
            sections.append(f"[apkid]\n{_run_cmd(['apkid', apk_path])}")

        return "\n\n".join(sections) if sections else "Error: aapt/aapt2 not found"


# ── APK Decompile (smali) ──────────────────────────────────────────────────


class APKDecompileInput(BaseModel):
    apk_path: str = Field(description="Path to the APK file.")
    output_dir: str = Field(default="", description="Output directory for decompiled smali. Empty = auto-generate.")
    timeout: int = Field(default=300, description="Timeout in seconds.")


class APKDecompileTool(BaseTool):
    """Decompile an APK to smali bytecode using apktool."""

    name: str = "apk_decompile"
    description: str = (
        "Decompile an APK to smali bytecode using apktool. "
        "Extracts resources, AndroidManifest.xml, and smali source. "
        "Install: brew install apktool  OR  apt install apktool"
    )
    args_schema: ClassVar[type[BaseModel]] = APKDecompileInput

    def _run(self, apk_path: str, output_dir: str = "", timeout: int = 300) -> str:
        import shutil
        if not shutil.which("apktool"):
            return "Error: apktool not found. Install: brew install apktool"
        out_path = output_dir or str(Path(apk_path).stem + "_smali")
        return _run_cmd(["apktool", "d", apk_path, "-o", out_path, "-f"], timeout=timeout)


# ── APK Decompile Java ─────────────────────────────────────────────────────


class APKDecompileJavaInput(BaseModel):
    apk_path: str = Field(description="Path to the APK file.")
    output_dir: str = Field(default="", description="Output directory for Java source. Empty = auto-generate.")
    timeout: int = Field(default=600, description="Timeout in seconds.")


class APKDecompileJavaTool(BaseTool):
    """Decompile an APK to Java source using jadx."""

    name: str = "apk_decompile_java"
    description: str = (
        "Decompile an APK to readable Java source code using jadx. "
        "Much more readable than smali — use for code review. "
        "Install: brew install jadx  OR  apt install jadx"
    )
    args_schema: ClassVar[type[BaseModel]] = APKDecompileJavaInput

    def _run(self, apk_path: str, output_dir: str = "", timeout: int = 600) -> str:
        import shutil
        if not shutil.which("jadx"):
            return "Error: jadx not found. Install: brew install jadx"
        out_path = output_dir or str(Path(apk_path).stem + "_java")
        return _run_cmd(["jadx", "-d", out_path, apk_path], timeout=timeout)


# ── ADB Shell ──────────────────────────────────────────────────────────────


class ADBShellInput(BaseModel):
    command: str = Field(description="Shell command to run via adb, e.g. 'id', 'pm list packages -3'.")
    device: str = Field(default="", description="Device serial (adb -s). Empty = use the only connected device.")
    timeout: int = Field(default=30, description="Timeout in seconds.")


class ADBShellTool(BaseTool):
    """Run shell commands on a connected Android device via adb."""

    name: str = "adb_shell"
    description: str = (
        "Run shell commands on a connected Android device via adb. "
        "Examples: 'id', 'pm list packages -3', 'dumpsys activity', 'getprop ro.build.version.release'. "
        "Requires adb and a connected device / emulator."
    )
    args_schema: ClassVar[type[BaseModel]] = ADBShellInput

    def _run(self, command: str, device: str = "", timeout: int = 30) -> str:
        import shutil
        if not shutil.which("adb"):
            return "Error: adb not found. Install Android SDK platform-tools."
        cmd = ["adb"]
        if device:
            cmd.extend(["-s", device])
        cmd.extend(["shell"] + command.split())
        return _run_cmd(cmd, timeout=timeout)


# ── Android Manifest Audit ─────────────────────────────────────────────────


class AndroidManifestAuditInput(BaseModel):
    apk_path: str = Field(description="Path to the APK file.")
    timeout: int = Field(default=60, description="Timeout in seconds.")


class AndroidManifestAuditTool(BaseTool):
    """Audit AndroidManifest.xml for common security misconfigurations."""

    name: str = "android_manifest_audit"
    description: str = (
        "Audit AndroidManifest.xml for security misconfigurations: "
        "debuggable=true, allowBackup=true, exported components without permissions, "
        "cleartext traffic, and legacy min SDK. "
        "Uses apktool to extract the manifest."
    )
    args_schema: ClassVar[type[BaseModel]] = AndroidManifestAuditInput

    def _run(self, apk_path: str, timeout: int = 60) -> str:
        import shutil
        if not shutil.which("apktool"):
            return "Error: apktool not found. Install: brew install apktool"

        with tempfile.TemporaryDirectory() as tmp:
            out = _run_cmd(["apktool", "d", apk_path, "-o", tmp, "-f", "--no-src"], timeout=timeout)
            manifest_path = Path(tmp) / "AndroidManifest.xml"
            if not manifest_path.is_file():
                return f"Could not extract AndroidManifest.xml.\napktool output:\n{out}"
            manifest = manifest_path.read_text(encoding="utf-8", errors="replace")

        findings: list[str] = []

        if 'android:debuggable="true"' in manifest:
            findings.append("[CRITICAL] android:debuggable=true — app can be debugged by any process")
            _add_finding(
                title="Android: debuggable=true",
                description=f"APK {apk_path} has android:debuggable=true in manifest",
                severity="critical",
                tool="android_manifest_audit",
            )

        if 'android:allowBackup="true"' in manifest:
            findings.append("[HIGH] android:allowBackup=true — data extractable via adb backup")
            _add_finding(
                title="Android: allowBackup=true",
                description=f"APK {apk_path} allows ADB backup (data extraction risk)",
                severity="high",
                tool="android_manifest_audit",
            )

        if 'android:usesCleartextTraffic="true"' in manifest:
            findings.append("[HIGH] usesCleartextTraffic=true — HTTP allowed")

        exported_no_perm = re.findall(
            r'<(?:activity|receiver|provider|service)[^>]+android:exported="true"[^>]*>',
            manifest,
        )
        exported_with_perm = [e for e in exported_no_perm if "android:permission" in e]
        exported_without = [e for e in exported_no_perm if "android:permission" not in e]
        if exported_without:
            findings.append(
                f"[MEDIUM] {len(exported_without)} exported component(s) without permission restriction"
            )

        min_sdk = re.search(r'android:minSdkVersion="(\d+)"', manifest)
        if min_sdk and int(min_sdk.group(1)) < 21:
            findings.append(f"[LOW] minSdkVersion={min_sdk.group(1)} — supports Android 4.x (legacy attack surface)")

        if not findings:
            return "No manifest security issues detected."
        return "AndroidManifest.xml audit findings:\n\n" + "\n".join(findings)


# ── Frida Inject ──────────────────────────────────────────────────────────


class FridaInjectInput(BaseModel):
    package: str = Field(description="Target Android package name, e.g. 'com.example.app'.")
    script_path: str = Field(description="Path to the Frida instrumentation script (.js).")
    device: str = Field(default="", description="Device serial (adb -s). Empty = default device.")
    timeout: int = Field(default=60, description="Frida session timeout in seconds.")


class FridaInjectTool(BaseTool):
    """Inject a Frida script into a running Android application for dynamic instrumentation."""

    name: str = "frida_inject"
    description: str = (
        "Inject a Frida script into a running Android app for dynamic instrumentation. "
        "Use for SSL pinning bypass, root detection bypass, API hooking, and runtime analysis. "
        "Requires frida: pip install frida-tools  AND  frida-server running on the device."
    )
    args_schema: ClassVar[type[BaseModel]] = FridaInjectInput

    def _run(self, package: str, script_path: str, device: str = "", timeout: int = 60) -> str:
        import shutil
        if not shutil.which("frida"):
            return "Error: frida not found. Install: pip install frida-tools"
        if not Path(script_path).is_file():
            return f"Error: script not found: {script_path}"
        cmd = ["frida", "-U", "-l", script_path, "-n", package, "--no-pause"]
        if device:
            cmd = ["frida", "-D", device, "-l", script_path, "-n", package, "--no-pause"]
        return _run_cmd(cmd, timeout=timeout)


# ── Factory ────────────────────────────────────────────────────────────────


def get_android_tools() -> list[BaseTool]:
    return [
        APKInfoTool(),
        APKDecompileTool(),
        APKDecompileJavaTool(),
        ADBShellTool(),
        AndroidManifestAuditTool(),
        FridaInjectTool(),
    ]
