from __future__ import annotations

import base64
import os
import subprocess
from typing import Any

from .base import DeploymentAdapter
from ..profiles import ApplicationProfile


class DigitalOceanDeploymentAdapter(DeploymentAdapter):
    provider = "digitalocean"
    base_url = "https://api.digitalocean.com/v2"

    @staticmethod
    def run_ssh_command(
        host: str,
        port: int = 22,
        user: str = "root",
        command: str = "echo ok",
        key_data: str | None = None,
        timeout: int = 15
    ) -> tuple[int, str, str]:
        cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", f"ConnectTimeout={min(timeout, 5)}",
            "-p", str(port),
        ]
        key_file = None
        try:
            if key_data and key_data.strip():
                import tempfile
                key_file = tempfile.NamedTemporaryFile("w", delete=False)
                key_file.write(key_data.strip() + "\n")
                key_file.flush()
                key_file.close()
                cmd.extend(["-i", key_file.name])
            cmd.extend([f"{user}@{host}", command])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"SSH connection to {user}@{host}:{port} timed out after {timeout} seconds."
        except Exception as e:
            return 1, "", str(e)
        finally:
            if key_file:
                try:
                    os.unlink(key_file.name)
                except Exception:
                    pass

    @classmethod
    def test_ssh_connection(
        cls,
        host: str,
        port: int = 22,
        user: str = "root",
        key_data: str | None = None,
        timeout: int = 8
    ) -> dict[str, Any]:
        if not host or not str(host).strip():
            return {"authenticated": False, "message": "No host or IP address specified."}
        host = str(host).strip()
        code, stdout, stderr = cls.run_ssh_command(
            host, port, user,
            "uname -srm; uptime -p 2>/dev/null || uptime; df -h / | tail -n 1; docker --version 2>/dev/null || echo 'no-docker'",
            key_data=key_data,
            timeout=timeout
        )
        if code == 0:
            lines = [l.strip() for l in stdout.strip().splitlines() if l.strip()]
            os_info = lines[0] if lines else "Linux"
            uptime_info = lines[1] if len(lines) > 1 else ""
            disk_line = lines[2] if len(lines) > 2 else ""
            disk_avail = disk_line.split()[3] if len(disk_line.split()) >= 4 else "unknown"
            docker_info = lines[3] if len(lines) > 3 else "unknown"
            return {
                "authenticated": True,
                "host": host,
                "port": port,
                "user": user,
                "message": f"SSH connection to {user}@{host}:{port} OK. {os_info}. Free disk: {disk_avail}.",
                "details": {
                    "os": os_info,
                    "uptime": uptime_info,
                    "disk_available": disk_avail,
                    "docker": docker_info,
                }
            }
        err_msg = stderr.strip() or stdout.strip() or f"SSH exited with code {code}"
        return {
            "authenticated": False,
            "host": host,
            "port": port,
            "user": user,
            "message": f"SSH connection to {user}@{host}:{port} failed: {err_msg}",
            "error": err_msg
        }

    def validate(self, host: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"authenticated": False}
        if self.token:
            try:
                account = self.request("GET", f"{self.base_url}/account")
                regions = self.request("GET", f"{self.base_url}/regions?per_page=200")
                sizes = self.request("GET", f"{self.base_url}/sizes?per_page=200")
                result.update({
                    "authenticated": account.get("account", {}).get("status") == "active",
                    "account": account.get("account", {}),
                    "regions": regions.get("regions", []),
                    "sizes": sizes.get("sizes", []),
                })
            except Exception as e:
                result["api_error"] = str(e)

        if host:
            ssh_test = self.test_ssh_connection(host)
            result["ssh"] = ssh_test
            if ssh_test.get("authenticated"):
                result["authenticated"] = True

        if not self.token and not host:
            result["authenticated"] = False

        return result

    def get_droplet(self, droplet_id: str | int) -> dict[str, Any]:
        res = self.request("GET", f"{self.base_url}/droplets/{droplet_id}")
        return res.get("droplet", {})

    def get_droplet_ip(self, droplet_id: str | int) -> str | None:
        droplet = self.get_droplet(droplet_id)
        for net in droplet.get("networks", {}).get("v4", []):
            if net.get("type") == "public":
                return net.get("ip_address")
        return None

    def provision(self, profile: ApplicationProfile, config: dict[str, Any], repository: str, environment: dict[str, str]) -> dict[str, Any]:
        strip_chars = '`"\'* \t\r\n'
        raw_host = str(config.get("host") or config.get("server_ip") or config.get("droplet_ip") or "")
        host = raw_host.strip(strip_chars).replace("`", "").replace("*", "")
        if host:
            port = int(str(config.get("ssh_port") or 22).strip(strip_chars).replace("*", ""))
            user = str(config.get("ssh_user") or "root").strip(strip_chars).replace("`", "").replace("*", "")
            key_data = config.get("ssh_key")
            default_dir = "/var/www/reportingtool-webservice" if profile.key == "services" else "/var/www/reportingtool-admin"
            raw_target_dir = str(config.get("target_dir") or "").strip(strip_chars).replace("`", "").replace("*", "")
            target_dir = raw_target_dir if raw_target_dir else default_dir
            domain = str(config.get("domain") or "").strip(strip_chars).replace("`", "").replace("*", "")
            ssl = bool(config.get("ssl", False))
            if profile.key == "administration":
                url = f"https://{domain}" if (ssl and domain) else "https://admin-reporting.vtabsquare.com"
            else:
                url = (f"https://{domain}" if ssl else f"http://{domain}") if domain else f"http://{host}:{profile.port}"

            test = self.test_ssh_connection(host, port, user, key_data)
            if not test.get("authenticated"):
                raise RuntimeError(f"Cannot deploy to {user}@{host}:{port} — {test.get('message', 'SSH failed')}")

            self.run_ssh_command(host, port, user, f"mkdir -p '{target_dir}'", key_data=key_data)

            # Ensure essential runtime tools exist on the host (Docker, Nginx, Node.js, Python3 venv) if deploying to a clean server
            ensure_tools_cmd = (
                "which docker >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq docker.io && systemctl enable --now docker); "
                "which nginx >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq nginx && systemctl enable --now nginx); "
                "which node >/dev/null 2>&1 || (curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y -qq nodejs); "
                "which python3 >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv); "
                "dpkg -s python3-venv >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq python3-venv python3-pip)"
            )
            self.run_ssh_command(host, port, user, ensure_tools_cmd, key_data=key_data, timeout=180)

            # Git clone or init/fetch if not present, then checkout approved commit
            commit_sha = str(config.get("approved_commit_sha") or "").strip(strip_chars).replace("`", "")
            if repository:
                clone_cmd = (
                    f"if [ ! -d '{target_dir}/.git' ]; then "
                    f"mkdir -p '{target_dir}' && cd '{target_dir}' && git init && "
                    f"(git remote add origin '{repository}' 2>/dev/null || git remote set-url origin '{repository}') && "
                    f"git fetch origin 2>&1; "
                    f"fi"
                )
                self.run_ssh_command(host, port, user, clone_cmd, key_data=key_data, timeout=60)
            if commit_sha:
                git_cmd = f"cd '{target_dir}' && if [ -d .git ]; then git fetch origin 2>&1 && git reset --hard {commit_sha} 2>&1; fi"
                self.run_ssh_command(host, port, user, git_cmd, key_data=key_data, timeout=30)

            # Rebuild frontend web distribution if source package exists
            if profile.key == "services":
                build_cmd = f"if [ -f '{target_dir}/package.json' ]; then cd '{target_dir}' && npm run build:web 2>&1 && chown -R www-data:www-data '{target_dir}/dist-web' 2>/dev/null || true; fi"
                self.run_ssh_command(host, port, user, build_cmd, key_data=key_data, timeout=120)
            elif profile.key == "administration":
                build_cmd = f"if [ -f '{target_dir}/package.json' ]; then cd '{target_dir}' && (npm ci 2>&1 || npm install 2>&1) && npm run build 2>&1 && chown -R www-data:www-data '{target_dir}/dist' 2>/dev/null || true; fi"
                self.run_ssh_command(host, port, user, build_cmd, key_data=key_data, timeout=120)

            # Atomic environment configuration (strip any accidental markdown backticks or quotes)
            backend_dir = f"{target_dir}/backend" if profile.key == "administration" else target_dir
            self.run_ssh_command(host, port, user, f"mkdir -p '{backend_dir}'", key_data=key_data)
            env_file = f"{target_dir}/web.env" if profile.key == "services" else f"{backend_dir}/.env"

            # Preserve existing server configuration keys if not explicitly provided
            check_env_code, check_env_out, _ = self.run_ssh_command(host, port, user, f"cat '{env_file}' 2>/dev/null || true", key_data=key_data, timeout=10)
            if check_env_code == 0 and check_env_out:
                for line in check_env_out.splitlines():
                    line = line.strip()
                    if line and "=" in line and not line.startswith("#"):
                        ek, _, ev = line.partition("=")
                        ek = ek.strip().strip(strip_chars).replace("*", "")
                        ev = ev.strip().strip(strip_chars).replace("`", "").replace("*", "")
                        if ek and ek not in environment:
                            environment[ek] = ev

            if profile.key == "services":
                environment.setdefault("VTAB_AGENT_TOKEN", "SDCsfIsSnlOj4ceWprbj-0GrsjxESrOT1DnmNJOGa_Y")
                environment.setdefault("VTAB_MANAGER_URL", "https://manager.139.59.32.39.nip.io")
                environment.setdefault("VTAB_AUTO_OFFER_GITHUB", "false")
                environment.setdefault("VTAB_SIMULATE_BUILD", "0")
                environment.setdefault("ADMIN_OTP_ENABLED", "1")
                environment.setdefault("ADMIN_EMAIL", "harishkadhi18022001@gmail.com")
                environment.setdefault("VTAB_SMTP_FROM", "aivtabsquare@gmail.com")
                environment.setdefault("SUPABASE_URL", "https://asjscibujiikthxmpxgg.supabase.co")
                environment.setdefault("VITE_SUPABASE_URL", environment["SUPABASE_URL"])
                resolved_web_url = f"https://{domain}" if (ssl and domain) else (f"http://{domain}" if domain else "https://reportservices.vtabsquare.com")
                environment.setdefault("VTAB_WEB_URL", resolved_web_url)
                if commit_sha:
                    environment["VTAB_RELEASE_COMMIT_SHA"] = commit_sha[:8]
                rel_ver = str(config.get("version") or config.get("target_version") or "").replace("commit-", "")
                if rel_ver:
                    environment["VTAB_RELEASE_VERSION"] = rel_ver
                else:
                    environment.setdefault("VTAB_RELEASE_VERSION", "6.1.12")
                for render_k in ("RENDER_SERVICE_ID", "VTAB_RENDER_SERVICE_ID", "RENDER_API_KEY", "RENDER_TOKEN", "RENDER_API_TOKEN"):
                    environment.pop(render_k, None)
            elif profile.key == "administration":
                environment.setdefault("VTAB_AGENT_TOKEN", "SDCsfIsSnlOj4ceWprbj-0GrsjxESrOT1DnmNJOGa_Y")
                environment.setdefault("VTAB_MANAGER_URL", "https://manager.139.59.32.39.nip.io")
                environment.setdefault("VTAB_CLIENT_ID", "65805eab-d112-4b8f-b46d-a8f2dcd8cf0b")
                environment.setdefault("VTAB_AUTO_OFFER_GITHUB", "false")
                environment.setdefault("VTAB_SIMULATE_BUILD", "0")
                environment.setdefault("ADMIN_OTP_ENABLED", "1")
                environment.setdefault("ADMIN_EMAIL", "harishkadhi18022001@gmail.com")
                environment.setdefault("VTAB_SMTP_FROM", "aivtabsquare@gmail.com")
                environment.setdefault("SUPABASE_URL", "https://asjscibujiikthxmpxgg.supabase.co")
                environment.setdefault("VITE_SUPABASE_URL", environment["SUPABASE_URL"])
                environment.setdefault("PORT", "8835")
                environment.setdefault("SERVICES_API_URL", "https://reportservices.vtabsquare.com/api/v1")
                resolved_admin_url = f"https://{domain}" if (ssl and domain) else "https://admin-reporting.vtabsquare.com"
                environment.setdefault("VTAB_ADMIN_URL", resolved_admin_url)
                if commit_sha:
                    environment["VTAB_RELEASE_COMMIT_SHA"] = commit_sha[:8]
                rel_ver = str(config.get("version") or config.get("target_version") or "").replace("commit-", "")
                if rel_ver:
                    environment["VTAB_ADMIN_VERSION"] = rel_ver
                else:
                    environment.setdefault("VTAB_ADMIN_VERSION", "2.2.0")
                for render_k in ("RENDER_SERVICE_ID", "VTAB_RENDER_SERVICE_ID", "RENDER_API_KEY", "RENDER_TOKEN", "RENDER_API_TOKEN"):
                    environment.pop(render_k, None)

            env_lines = [f"{k.strip(strip_chars).replace('*', '')}={str(v).strip(strip_chars).replace('`', '').replace('*', '')}" for k, v in sorted(environment.items())]
            env_content = "\n".join(env_lines) + "\n"
            b64_env = base64.b64encode(env_content.encode("utf-8")).decode("ascii")
            write_cmd = f"echo '{b64_env}' | base64 -d > '{env_file}' && chmod 600 '{env_file}'"
            self.run_ssh_command(host, port, user, write_cmd, key_data=key_data)

            # Target service lifecycle and Nginx routing
            if profile.key == "services":
                container = "vtab-reporting-api"
                agent_token = environment.get("VTAB_AGENT_TOKEN", "SDCsfIsSnlOj4ceWprbj-0GrsjxESrOT1DnmNJOGa_Y")

                # Ensure deploy.sh and dist-web permissions
                setup_perms_cmd = (
                    f"chmod +x '{target_dir}/deploy.sh' 2>/dev/null || true; "
                    f"chown -R www-data:www-data '{target_dir}/dist-web' 2>/dev/null || true; "
                    f"chmod -R 755 '{target_dir}/dist-web' 2>/dev/null || true"
                )
                self.run_ssh_command(host, port, user, setup_perms_cmd, key_data=key_data)

                # Ensure systemd host deploy trigger service (port 8829) is active with current agent token
                trigger_content = (
                    f"[Unit]\n"
                    f"Description=VTAB Services Host Deploy Trigger\n"
                    f"After=network.target docker.service\n\n"
                    f"[Service]\n"
                    f"Type=simple\n"
                    f"User=root\n"
                    f"WorkingDirectory={target_dir}\n"
                    f"Environment=VTAB_AGENT_TOKEN={agent_token}\n"
                    f"ExecStart=/usr/bin/python3 {target_dir}/scripts/deploy_trigger_service.py\n"
                    f"Restart=always\n"
                    f"RestartSec=3\n\n"
                    f"[Install]\n"
                    f"WantedBy=multi-user.target\n"
                )
                b64_trigger = base64.b64encode(trigger_content.encode("utf-8")).decode("ascii")
                trigger_cmd = (
                    f"echo '{b64_trigger}' | base64 -d > /etc/systemd/system/vtab-deploy-trigger.service && "
                    f"systemctl daemon-reload && systemctl enable --now vtab-deploy-trigger.service && "
                    f"systemctl restart vtab-deploy-trigger.service 2>/dev/null || true"
                )
                self.run_ssh_command(host, port, user, trigger_cmd, key_data=key_data)

                # Ensure Nginx site is enabled for reportingtool (create default configuration if missing)
                nginx_site_conf = (
                    f"server {{\n"
                    f"    server_name {domain or 'reportservices.vtabsquare.com'};\n\n"
                    f"    client_max_body_size 100M;\n"
                    f"    client_body_buffer_size 16M;\n"
                    f"    client_body_timeout 120s;\n\n"
                    f"    root {target_dir}/dist-web;\n"
                    f"    index index.html;\n\n"
                    f"    location = /index.html {{\n"
                    f'        add_header Cache-Control "no-store, no-cache, must-revalidate" always;\n'
                    f'        add_header Pragma "no-cache" always;\n'
                    f"        try_files $uri =404;\n"
                    f"    }}\n\n"
                    f"    location /assets/ {{\n"
                    f'        add_header Cache-Control "public, max-age=31536000, immutable" always;\n'
                    f"        try_files $uri =404;\n"
                    f"    }}\n\n"
                    f"    location / {{\n"
                    f'        add_header Cache-Control "no-store, no-cache, must-revalidate" always;\n'
                    f'        add_header Pragma "no-cache" always;\n'
                    f"        try_files $uri $uri/ /index.html;\n"
                    f"    }}\n\n"
                    f"    location /api/ {{\n"
                    f"        client_max_body_size 100M;\n"
                    f"        proxy_pass http://127.0.0.1:{profile.port}/api/;\n"
                    f"        proxy_http_version 1.1;\n"
                    f"        proxy_set_header Host $host;\n"
                    f"        proxy_set_header X-Real-IP $remote_addr;\n"
                    f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                    f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
                    f"        proxy_connect_timeout 10s;\n"
                    f"        proxy_send_timeout 300s;\n"
                    f"        proxy_read_timeout 300s;\n"
                    f"    }}\n\n"
                    f"    listen 80;\n"
                    f"}}\n"
                )
                b64_nginx = base64.b64encode(nginx_site_conf.encode("utf-8")).decode("ascii")
                nginx_cmd = (
                    f"if [ ! -f /etc/nginx/sites-available/reportingtool ]; then "
                    f"echo '{b64_nginx}' | base64 -d > /etc/nginx/sites-available/reportingtool; "
                    f"fi; "
                    f"ln -sf /etc/nginx/sites-available/reportingtool /etc/nginx/sites-enabled/reportingtool; "
                    f"rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true; "
                    f"nginx -t && systemctl reload nginx 2>/dev/null || true"
                )
                self.run_ssh_command(host, port, user, nginx_cmd, key_data=key_data)

                # Ensure Docker image exists; build if missing
                build_image_cmd = f"if [ ! $(docker images -q vtab-reporting-api:latest 2>/dev/null) ]; then cd '{target_dir}/api' && docker build -t vtab-reporting-api:latest . 2>&1; fi"
                self.run_ssh_command(host, port, user, build_image_cmd, key_data=key_data, timeout=180)

                # Re-launch container with fresh environment and mount
                restart_cmd = (
                    f"docker rm -f {container} 2>/dev/null || true; "
                    f"docker run -d --name {container} --restart always -p {profile.port}:{profile.port} "
                    f"--env-file '{env_file}' -v '{target_dir}/api:/app' vtab-reporting-api:latest"
                )
                code, out, err = self.run_ssh_command(host, port, user, restart_cmd, key_data=key_data, timeout=30)
                if code != 0:
                    raise RuntimeError(f"Failed to launch container {container}: {err or out}")
            else:
                # Administration tool
                # Ensure Python virtual environment exists and dependencies are installed
                setup_venv_cmd = (
                    f"if [ ! -d '{backend_dir}/venv' ]; then python3 -m venv '{backend_dir}/venv'; fi; "
                    f"'{backend_dir}/venv/bin/pip' install --upgrade pip -q; "
                    f"if [ -f '{backend_dir}/requirements.txt' ]; then '{backend_dir}/venv/bin/pip' install -r '{backend_dir}/requirements.txt' -q; fi"
                )
                self.run_ssh_command(host, port, user, setup_venv_cmd, key_data=key_data, timeout=180)

                # Ensure deploy script and dist permissions
                setup_perms_cmd = (
                    f"chmod +x '{target_dir}/deploy.sh' '{target_dir}/deploy/deploy.sh' 2>/dev/null || true; "
                    f"chown -R www-data:www-data '{target_dir}/dist' 2>/dev/null || true; "
                    f"chmod -R 755 '{target_dir}/dist' 2>/dev/null || true"
                )
                self.run_ssh_command(host, port, user, setup_perms_cmd, key_data=key_data)

                # Ensure systemd service unit exists
                admin_unit = (
                    f"[Unit]\n"
                    f"Description=VTAB Reporting Tool Admin API Backend\n"
                    f"After=network.target\n\n"
                    f"[Service]\n"
                    f"Type=simple\n"
                    f"User=root\n"
                    f"WorkingDirectory={backend_dir}\n"
                    f"EnvironmentFile={env_file}\n"
                    f"ExecStart={backend_dir}/venv/bin/uvicorn main:app --host 127.0.0.1 --port {profile.port}\n"
                    f"Restart=always\n"
                    f"RestartSec=3\n\n"
                    f"[Install]\n"
                    f"WantedBy=multi-user.target\n"
                )
                b64_unit = base64.b64encode(admin_unit.encode("utf-8")).decode("ascii")
                unit_cmd = (
                    f"echo '{b64_unit}' | base64 -d > /etc/systemd/system/reportingtool-admin.service && "
                    f"systemctl daemon-reload && systemctl enable --now reportingtool-admin.service && "
                    f"systemctl restart reportingtool-admin.service"
                )
                code, out, err = self.run_ssh_command(host, port, user, unit_cmd, key_data=key_data, timeout=30)
                if code != 0:
                    raise RuntimeError(f"Failed to start reportingtool-admin.service: {err or out}")

                # Ensure Nginx reverse proxy configuration exists (HTTP and HTTPS)
                admin_nginx_conf = (
                    f"server {{\n"
                    f"    listen 80;\n"
                    f"    server_name {domain or 'admin-reporting.139.59.32.39.nip.io'} admin-reporting.139.59.32.39.nip.io admin-reporting.139.59.32.39.sslip.io admin-reporting.vtabsquare.com;\n\n"
                    f"    client_max_body_size 50M;\n"
                    f"    client_body_buffer_size 16M;\n\n"
                    f"    root {target_dir}/dist;\n"
                    f"    index index.html;\n\n"
                    f"    location = /index.html {{\n"
                    f'        add_header Cache-Control "no-store, no-cache, must-revalidate" always;\n'
                    f'        add_header Pragma "no-cache" always;\n'
                    f"        try_files $uri =404;\n"
                    f"    }}\n\n"
                    f"    location /assets/ {{\n"
                    f'        add_header Cache-Control "public, max-age=31536000, immutable" always;\n'
                    f"        try_files $uri =404;\n"
                    f"    }}\n\n"
                    f"    location / {{\n"
                    f'        add_header Cache-Control "no-store, no-cache, must-revalidate" always;\n'
                    f'        add_header Pragma "no-cache" always;\n'
                    f"        try_files $uri $uri/ /index.html;\n"
                    f"    }}\n\n"
                    f"    location /api/ {{\n"
                    f"        client_max_body_size 50M;\n"
                    f"        proxy_pass http://127.0.0.1:{profile.port}/api/;\n"
                    f"        proxy_http_version 1.1;\n"
                    f"        proxy_set_header Host $host;\n"
                    f"        proxy_set_header X-Real-IP $remote_addr;\n"
                    f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                    f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
                    f"        proxy_connect_timeout 10s;\n"
                    f"        proxy_send_timeout 120s;\n"
                    f"        proxy_read_timeout 120s;\n"
                    f"    }}\n"
                    f"}}\n\n"
                    f"server {{\n"
                    f"    listen 443 ssl http2;\n"
                    f"    server_name admin-reporting.vtabsquare.com;\n\n"
                    f"    ssl_certificate /etc/letsencrypt/live/admin-reporting.vtabsquare.com/fullchain.pem;\n"
                    f"    ssl_certificate_key /etc/letsencrypt/live/admin-reporting.vtabsquare.com/privkey.pem;\n"
                    f"    include /etc/letsencrypt/options-ssl-nginx.conf;\n"
                    f"    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;\n\n"
                    f"    client_max_body_size 50M;\n"
                    f"    client_body_buffer_size 16M;\n\n"
                    f"    root {target_dir}/dist;\n"
                    f"    index index.html;\n\n"
                    f"    location = /index.html {{\n"
                    f'        add_header Cache-Control "no-store, no-cache, must-revalidate" always;\n'
                    f'        add_header Pragma "no-cache" always;\n'
                    f"        try_files $uri =404;\n"
                    f"    }}\n\n"
                    f"    location /assets/ {{\n"
                    f'        add_header Cache-Control "public, max-age=31536000, immutable" always;\n'
                    f"        try_files $uri =404;\n"
                    f"    }}\n\n"
                    f"    location / {{\n"
                    f'        add_header Cache-Control "no-store, no-cache, must-revalidate" always;\n'
                    f'        add_header Pragma "no-cache" always;\n'
                    f"        try_files $uri $uri/ /index.html;\n"
                    f"    }}\n\n"
                    f"    location /api/ {{\n"
                    f"        client_max_body_size 50M;\n"
                    f"        proxy_pass http://127.0.0.1:{profile.port}/api/;\n"
                    f"        proxy_http_version 1.1;\n"
                    f"        proxy_set_header Host $host;\n"
                    f"        proxy_set_header X-Real-IP $remote_addr;\n"
                    f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                    f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
                    f"        proxy_connect_timeout 10s;\n"
                    f"        proxy_send_timeout 120s;\n"
                    f"        proxy_read_timeout 120s;\n"
                    f"    }}\n"
                    f"}}\n\n"
                    f"server {{\n"
                    f"    listen 443 ssl http2;\n"
                    f"    server_name admin-reporting.139.59.32.39.nip.io admin-reporting.139.59.32.39.sslip.io;\n\n"
                    f"    ssl_certificate /etc/letsencrypt/live/admin-reporting.139.59.32.39.nip.io/fullchain.pem;\n"
                    f"    ssl_certificate_key /etc/letsencrypt/live/admin-reporting.139.59.32.39.nip.io/privkey.pem;\n"
                    f"    include /etc/letsencrypt/options-ssl-nginx.conf;\n"
                    f"    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;\n\n"
                    f"    client_max_body_size 50M;\n"
                    f"    client_body_buffer_size 16M;\n\n"
                    f"    root {target_dir}/dist;\n"
                    f"    index index.html;\n\n"
                    f"    location = /index.html {{\n"
                    f'        add_header Cache-Control "no-store, no-cache, must-revalidate" always;\n'
                    f'        add_header Pragma "no-cache" always;\n'
                    f"        try_files $uri =404;\n"
                    f"    }}\n\n"
                    f"    location /assets/ {{\n"
                    f'        add_header Cache-Control "public, max-age=31536000, immutable" always;\n'
                    f"        try_files $uri =404;\n"
                    f"    }}\n\n"
                    f"    location / {{\n"
                    f'        add_header Cache-Control "no-store, no-cache, must-revalidate" always;\n'
                    f'        add_header Pragma "no-cache" always;\n'
                    f"        try_files $uri $uri/ /index.html;\n"
                    f"    }}\n\n"
                    f"    location /api/ {{\n"
                    f"        client_max_body_size 50M;\n"
                    f"        proxy_pass http://127.0.0.1:{profile.port}/api/;\n"
                    f"        proxy_http_version 1.1;\n"
                    f"        proxy_set_header Host $host;\n"
                    f"        proxy_set_header X-Real-IP $remote_addr;\n"
                    f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                    f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
                    f"        proxy_connect_timeout 10s;\n"
                    f"        proxy_send_timeout 120s;\n"
                    f"        proxy_read_timeout 120s;\n"
                    f"    }}\n"
                    f"}}\n"
                )
                b64_nginx = base64.b64encode(admin_nginx_conf.encode("utf-8")).decode("ascii")
                nginx_cmd = (
                    f"echo '{b64_nginx}' | base64 -d > /etc/nginx/sites-available/reportingtool-admin && "
                    f"ln -sf /etc/nginx/sites-available/reportingtool-admin /etc/nginx/sites-enabled/reportingtool-admin && "
                    f"nginx -t && systemctl reload nginx 2>/dev/null || true"
                )
                self.run_ssh_command(host, port, user, nginx_cmd, key_data=key_data)

            return {
                "resource_id": f"droplet-{host}",
                "host": host,
                "url": url,
                "dashboard_url": f"http://{host}",
                "raw_status": "deployed",
                "deploy_type": "ssh_direct",
            }

        user_data = self.cloud_init(profile, repository, environment, config)
        service_name = str(config.get("service_name") or f"vtab-{profile.key}").strip()
        payload = {
            "name": service_name,
            "region": config.get("region", "blr1"),
            "size": config.get("instance_size", "s-2vcpu-4gb"),
            "image": config.get("image", "ubuntu-24-04-x64"),
            "ssh_keys": config.get("ssh_keys", []),
            "backups": True,
            "ipv6": True,
            "monitoring": True,
            "tags": ["vtab", profile.key],
            "user_data": user_data,
        }
        response = self.request("POST", f"{self.base_url}/droplets", payload)
        droplet = response.get("droplet", {})
        droplet_id = str(droplet.get("id", ""))
        domain = str(config.get("domain") or "").strip()
        ssl = bool(config.get("ssl", True))
        url = (f"https://{domain}" if ssl else f"http://{domain}") if domain else None
        return {
            "resource_id": droplet_id,
            "url": url,
            "dashboard_url": f"https://cloud.digitalocean.com/droplets/{droplet_id}",
            "raw_status": droplet.get("status", "accepted"),
        }

    def update_environment(self, resource_id: str, environment: dict[str, str], config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = config or {}
        host = str(config.get("host") or config.get("server_ip") or "").strip()
        if host:
            port = int(config.get("ssh_port") or 22)
            user = str(config.get("ssh_user") or "root").strip()
            key_data = config.get("ssh_key")
            target_dir = str(config.get("target_dir") or "").strip()
            if not target_dir:
                target_dir = "/var/www/reportingtool-webservice" if ("VITE_APP_MODE" in environment or "PORT" in environment) else "/var/www/reportingtool-admin"
            env_file = f"{target_dir}/web.env" if ("VITE_APP_MODE" in environment or "PORT" in environment) else f"{target_dir}/backend/.env"
            env_lines = [f"{k}={v}" for k, v in sorted(environment.items())]
            env_content = "\n".join(env_lines) + "\n"
            b64_env = base64.b64encode(env_content.encode("utf-8")).decode("ascii")
            write_cmd = f"echo '{b64_env}' | base64 -d > '{env_file}' && chmod 600 '{env_file}'"
            code, out, err = self.run_ssh_command(host, port, user, write_cmd, key_data=key_data)
            if code != 0:
                raise RuntimeError(f"Failed to update environment on {host}: {err or out}")
            container = str(config.get("container_name") or "").strip()
            service = str(config.get("service_name") or "").strip()
            if container or service:
                r_cmd = f"docker restart {container} 2>/dev/null || systemctl restart {service} 2>/dev/null || true"
                self.run_ssh_command(host, port, user, r_cmd, key_data=key_data)
            return {"resource_id": resource_id, "status": "synced", "keys": sorted(environment)}

        return {"resource_id": resource_id, "status": "connector_sync_required", "keys": sorted(environment)}

    @staticmethod
    def cloud_init(profile: ApplicationProfile, repository: str, environment: dict[str, str], config: dict[str, Any]) -> str:
        secret_names = ", ".join(sorted(environment))
        domain = str(config.get("domain") or "").strip() or "_"
        frontend_dir = f"/var/www/{profile.key}/dist" if profile.key == "administration" else f"/var/www/{profile.key}/dist-web"
        return f"""#cloud-config
users:
  - name: vtab
    groups: [sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
package_update: true
packages:
  - nginx
  - ufw
  - python3
  - python3-venv
  - python3-pip
  - build-essential
  - git
  - curl
  - jq

write_files:
  - path: /opt/vtab/deployment-receipt.yml
    permissions: '0600'
    content: |
      component: {profile.key}
      repository: {repository}
      port: {profile.port}
      domain: {domain}
      required_secret_names: {secret_names}

  - path: /etc/nginx/sites-available/vtab-{profile.key}
    permissions: '0644'
    content: |
      server {{
          listen 80;
          listen [::]:80;
          server_name {domain};

          root {frontend_dir};
          index index.html;

          client_max_body_size 100M;
          client_body_buffer_size 16M;

          location = /index.html {{
              add_header Cache-Control "no-store, no-cache, must-revalidate" always;
              add_header Pragma "no-cache" always;
              try_files $uri =404;
          }}

          location /assets/ {{
              add_header Cache-Control "public, max-age=31536000, immutable" always;
              try_files $uri =404;
          }}

          location /api/ {{
              client_max_body_size 100M;
              proxy_pass http://127.0.0.1:{profile.port};
              proxy_http_version 1.1;
              proxy_set_header Host $host;
              proxy_set_header X-Real-IP $remote_addr;
              proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
              proxy_set_header X-Forwarded-Proto $scheme;
              proxy_connect_timeout 10s;
              proxy_send_timeout 300s;
              proxy_read_timeout 300s;
          }}

          location / {{
              add_header Cache-Control "no-store, no-cache, must-revalidate" always;
              add_header Pragma "no-cache" always;
              try_files $uri $uri/ /index.html;
          }}
      }}

runcmd:
  - [bash, -lc, 'ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable']
  - [bash, -lc, 'mkdir -p /opt/vtab /var/www/{profile.key} && chown -R vtab:vtab /opt/vtab /var/www/{profile.key}']
  - [bash, -lc, 'curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs']
  - [bash, -lc, 'ln -sf /etc/nginx/sites-available/vtab-{profile.key} /etc/nginx/sites-enabled/vtab-{profile.key}']
  - [bash, -lc, 'rm -f /etc/nginx/sites-enabled/default && nginx -t && systemctl reload nginx']
final_message: 'VTAB infrastructure bootstrap accepted; the outbound connector retrieves protected values from the client vault and completes installation.'
"""
