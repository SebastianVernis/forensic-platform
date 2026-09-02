# Deploy automatico del Motor de Analisis Forense

## 1. Preparar servidor

Requisitos minimos recomendados:
- CPU: 8+ cores (AVX2)
- RAM: 32 GB
- GPU: NVIDIA 12+ GB VRAM (recomendado; sin GPU el pipeline es lento pero funcional)
- Disco: SSD NVMe con 200 GB libres
- OS: Ubuntu 22.04/24.04 LTS o Debian 12
- Red: conexion estable para Ollama Cloud fallback (no necesita alto bandwidth)

## 2. Copiar archivos al servidor

```bash
scp lebaron_bundle.tar.zst deploy.sh run_pipeline.sh \
   requirements.txt .env.example motor-analisis-forense.service \
   root@tu-servidor:/opt/lebaron/
```

## 3. Ejecutar deploy

```bash
ssh root@tu-servidor
cd /opt/lebaron
chmod +x deploy.sh
./deploy.sh lebaron_bundle.tar.zst
```

Esto instala:
- Python, poppler-utils, tesseract-ocr, ocrmypdf, zstd
- Ollama local (solo localhost)
- Usuario `lebaron`
- Venv Python con dependencias
- Modelo `llama3.1:latest`
- `.env` a partir de `.env.example`

## 4. Configurar .env

```bash
nano /opt/lebaron/motor-analisis-forense/.env
```

Variables clave:
- `OLLAMA_CLOUD_API_KEY`: tu API key de Ollama Cloud
- `INPUT_DIR`: ruta al corpus (default `/opt/lebaron/L3B4_txt_raw`)
- `OUTPUT_DIR`: ruta a salida (default `/opt/lebaron/output`)
- `CHUNK_SIZE`: default 60000. Para CPU-only, usar 40000.
- `LLM_TIMEOUT`: default 600. Para cloud, 180.

## 5. Prueba manual con un solo tomo

```bash
su - lebaron -c '/opt/lebaron/motor-analisis-forense/run_pipeline.sh --tomos T30'
```

## 6. Pipeline completo

```bash
su - lebaron -c '/opt/lebaron/motor-analisis-forense/run_pipeline.sh'
```

## 7. Como servicio systemd

```bash
cp /opt/lebaron/motor-analisis-forense/motor-analisis-forense.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now motor-analisis-forense
systemctl status motor-analisis-forense
journalctl -u motor-analisis-forense -f
```

## 8. Hibridacion local/cloud

Por defecto el flujo usa:
- **Cloud** para `adapter.py` (perfil del caso): modelo grande, mejor razonamiento.
- **Local** para `analyzers/analizador_unificado.py` (chunks): barato y privado, con fallback a cloud si falla.

Para forzar todo local: dejar `OLLAMA_CLOUD_API_KEY` vacio.
Para forzar todo cloud: setear `MODELO_RAPIDO` y `MODELO_PROFUNDO` a modelos cloud y `OLLAMA_BASE_URL` a Ollama Cloud.

## 9. Seguridad

- `.env` tiene permisos 600.
- `output/` contiene datos sensibles (alias_map.json); hacer backup cifrado.
- Ollama local escucha solo en 127.0.0.1.
- El servicio corre con `ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`.

## 10. Backup

Antes de correr el pipeline completo:

```bash
tar --zstd -cf output_backup_$(date +%Y%m%d).tar.zst /opt/lebaron/output
```

## 11. Monitorizacion

```bash
tail -f /opt/lebaron/output/logs/run_*.log
watch -n 5 'curl -s http://localhost:11434/api/tags | head -c 200'
```
