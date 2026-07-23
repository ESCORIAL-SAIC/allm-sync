# allm-sync

Sincroniza en (casi) tiempo real los documentos de un **directorio de red de
Windows** con **workspaces de AnythingLLM** para RAG.

- Cada **carpeta de primer nivel** del share = un **workspace** de AnythingLLM.
- Los archivos dentro de esa carpeta (recursivo) se indexan en ese workspace.
- **Espejo completo**: altas, modificaciones y borrados del share se propagan.
- Corre como un container en la misma VM Debian/Docker que AnythingLLM.

> La asignación de permisos usuario→workspace se hace **a mano en la UI** de
> AnythingLLM (requiere multi-user mode). Este servicio sólo crea/actualiza los
> workspaces y sus documentos.

## Cómo funciona

Cada `poll_interval_seconds` (default 60s):

1. Escanea `ROOT_PATH` (el share montado por CIFS, read-only).
2. Por cada carpeta de primer nivel, asegura que exista el workspace (lo crea vía
   API si falta) y una carpeta de documentos.
3. Compara contra su estado local (SQLite):
   - **nuevo** → sube el archivo + lo embebe en el workspace,
   - **modificado** (cambió el sha256) → quita el viejo y sube/embebe el nuevo,
   - **borrado** (ya no está en el share) → lo quita del workspace y del storage.

El estado en SQLite persiste en un volumen, así que reiniciar el container **no**
re-sube todo. El chequeo rápido usa `size`+`mtime`; sólo se calcula el hash
cuando esos cambian.

## Requisitos

- AnythingLLM corriendo en Docker en la VM Debian, con **Developer API** activa y
  una **API key** generada (Settings → Tools → Developer API).
- Multi-user mode habilitado (para permisos por usuario).
- El módulo `cifs` disponible en el host Debian:
  `sudo apt-get install -y cifs-utils`
- Conectividad de red desde la VM Debian al fileserver de Windows (puerto 445).

## Configuración

Este repo ya viene con un `.env` completado para el entorno de Escorial
(`ALLM_BASE_URL=http://llm.escorialsa.com.ar`, share `//192.168.1.116/SGC Escorial`).
Verificá que los secretos sigan vigentes. Para otro entorno, partí de `.env.example`.

El container **no** necesita estar en la red interna de AnythingLLM: lo alcanza por
la URL pública. Si el container no lograra resolver ese hostname (DNS interno),
descomentá el bloque `extra_hosts` en `docker-compose.yml`.

Ajustá `config.yaml` si querés (extensiones, exclusiones, intervalo, `max_file_mb`,
`exclude_top_folders`).

## Probar antes de tocar nada (dry-run)

Poné `dry_run: true` en `config.yaml` (o `DRY_RUN=true` en `.env`) y levantá:

```bash
docker compose up --build
```

En los logs vas a ver qué workspaces crearía y qué archivos subiría/borraría, sin
modificar AnythingLLM. Cuando estés conforme, poné `dry_run: false`.

## Desplegar

```bash
docker compose up -d --build
docker compose logs -f allm-sync
```

## Verificación end-to-end

1. **Alta**: al primer ciclo aparecen los workspaces y sus documentos en la UI;
   un chat RAG los cita.
2. **Modificación**: editá un archivo en el share → tras un ciclo se re-embebe.
3. **Borrado**: borrá un archivo → desaparece del workspace y del storage.
4. **Resiliencia**: `docker compose restart allm-sync` no re-sube todo.
5. **Permisos**: asigná un usuario a un workspace en la UI y validá el acceso.

## Troubleshooting

- **`ROOT_PATH ... no existe`**: el montaje CIFS falló. Probá el share a mano:
  ```bash
  sudo mount -t cifs //SERVIDOR/Recurso /mnt/test \
    -o username=USUARIO,password=CLAVE,domain=DOMINIO,vers=3.0,ro
  ```
  Ajustá `SMB_VERS` (1.0/2.0/2.1/3.0) según el fileserver.
- **`auth=false` / no contacta AnythingLLM**: revisá `ALLM_BASE_URL`, que el
  container esté en la misma red (`allm_net`) y que `ALLM_API_KEY` sea válida.
- **Un path de la API falla (404)**: verificá los endpoints contra el Swagger de
  tu instancia en `http://<anythingllm>:3001/api/docs` y ajustá `src/allm_client.py`.
- **Caracteres raros en nombres**: el montaje usa `iocharset=utf8`.

## Estructura

```
allm-sync/
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── config.yaml
├── requirements.txt
└── src/
    ├── main.py         # loop + señales
    ├── config.py       # config.yaml + env
    ├── state.py        # SQLite (archivo ↔ documento)
    ├── scanner.py      # walk + hashing
    ├── allm_client.py  # API AnythingLLM
    └── reconciler.py   # lógica de espejo
```
