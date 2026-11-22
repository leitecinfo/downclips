# DownClip

Aplicación de escritorio local en Python y CustomTkinter para descargar videos, recortar VOD y grabar directos de YouTube, Twitch y Kick mediante yt-dlp y FFmpeg. La interfaz y las descargas están en `app.py`; la captura y los clips locales están en `live.py`; el historial HLS está en `dvr.py`.

## Ejecutable para Windows

Abre `dist/DownClip.exe` con doble clic. Es un ejecutable portable de un solo archivo con Python, CustomTkinter, yt-dlp, FFmpeg, ffprobe y Node.js incluidos; no requiere instalar estas herramientas ni configurar el PATH. Node.js se habilita en yt-dlp para resolver los retos JavaScript de YouTube. La primera apertura puede tardar unos segundos mientras extrae los componentes en una carpeta temporal. Conserva los selectores de horas, minutos y segundos. Los requisitos adicionales de cada sitio, como sesión de navegador o permisos de acceso, siguen dependiendo de ese sitio.

Para volver a compilar después de modificar el código, instala las dependencias de Python y PyInstaller, descarga el [yt-dlp.exe oficial](https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe) en `vendor/yt-dlp.exe` y ejecuta:

```powershell
python -m pip install pyinstaller
python build_exe.py
```

La máquina de compilación necesita FFmpeg, ffprobe y Node.js >=22 en el PATH. `build_exe.py` los incorpora al ejecutable junto con yt-dlp. El resultado queda en `dist/DownClip.exe`.

Para verificar el ejecutable compilado en Windows, ejecuta `python tests/verify_exe.py`. La prueba abre y cierra la ventana, comprueba las herramientas incluidas con un PATH limitado al sistema y verifica un recorte real con las herramientas extraídas del ejecutable.

## Instalación en Windows (PowerShell)

Requiere Python 3.11 o posterior con Tkinter y FFmpeg (incluido ffprobe) en el PATH.

```powershell
cd D:\Proyectos\downclip
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
winget install --id Gyan.FFmpeg --exact
```

Si FFmpeg ya está instalado, omite el último comando. Después de instalarlo, abre una terminal nueva, vuelve a la carpeta del proyecto y activa el entorno antes de iniciar:

```powershell
.\.venv\Scripts\Activate.ps1
ffmpeg -version
ffprobe -version
yt-dlp --version
python app.py
```

Si PowerShell bloquea la activación, puedes ejecutar desde **CMD**, sin cambiar la política del sistema:

```bat
cd /d D:\Proyectos\downclip
.venv\Scripts\activate.bat
python app.py
```

En Linux instala `python3-tk` y `ffmpeg` con el gestor de paquetes del sistema; crea y activa el entorno con `python3 -m venv .venv` y `source .venv/bin/activate`. En macOS usa una instalación de Python con Tkinter y FFmpeg. El resto de los comandos de pip y ejecución es el mismo.

## Uso

1. Pega una URL HTTP/HTTPS del video.
2. Elige el modo de descarga:
   - **Video completo (predeterminado):** En **Video / VOD**, la casilla *"Descargar video completo (sin recortar)"* comienza activada. Omite el recorte y la recodificación; la velocidad depende de la red, el sitio y el formato.
   - **Segmento específico:** Desmarca la casilla y selecciona **Horas**, **Minutos** y **Segundos** de inicio y fin con las listas desplegables.
3. Elige la **Calidad de video**:
   - `1080p (Rápido · Recomendado)`: Descarga en Full HD priorizando streams nativos MP4 (H.264/AAC).
   - `720p (Ultra rápido)`: Menor resolución y, normalmente, menor peso.
   - `480p (Ahorro de datos)`: Archivos ultraligeros.
   - `Mejor calidad / 4K`: Máxima resolución disponible en la plataforma.
4. Activa **Corte exacto al segundo** si necesitas ajustar el corte (`--force-keyframes-at-cuts`). Recodifica y puede consumir bastante CPU. Sin esta opción se copian los streams y el inicio puede ajustarse a un fotograma clave cercano.
5. Opcionalmente activa **Usar cookies del navegador (Kick/Cloudflare)** y elige `chrome`, `edge` o `brave`.
6. Elige una carpeta de destino y pulsa **Descargar**.

El registro muestra la descarga y el procesamiento en tiempo real. **Cancelar** detiene yt-dlp y sus procesos hijos de inmediato.

## Clips de directos mientras siguen en vivo

1. Pega el enlace del canal de Kick/Twitch o del directo de YouTube y elige **Directo en vivo**.
2. Elige la calidad y, si el sitio requiere sesión, las cookies. Pulsa **Conectar y grabar** antes del momento que quieras conservar.
3. El indicador muestra cuánto video se ha recibido realmente. **00:00 es el inicio del búfer local**, no el inicio de la emisión ni la hora del reloj. La captura puede tener retraso respecto al reproductor de la plataforma.
4. Usa **Marcar inicio** y **Marcar fin**, o **Seleccionar últimos 30 s**. También puedes ajustar horas, minutos y segundos manualmente, dentro del tramo ya grabado.
5. Pulsa **Guardar clip del directo**. La captura continúa mientras se genera el MP4; puedes guardar varios clips. Por defecto el corte es exacto y solo se recodifica el fragmento elegido.
6. **Detener grabación** finaliza el búfer, que queda conservado en la subcarpeta `Directos` del destino como `.ts`. Después puedes seguir recortándolo o recuperarlo con **Abrir grabación guardada…** en otra sesión. El búfer ocupa espacio en disco y cada sesión tiene un máximo de seis horas. Cancelar un clip no detiene la grabación; cerrar la aplicación sí detiene sus procesos y conserva los datos ya escritos.

**Momentos anteriores a la conexión:** en el panel del directo, usa **Recuperar clip del historial**. Indica **Hace cuánto empezó el clip** y su **Duración**: por ejemplo, hace 1 minuto y duración 30 segundos recupera el tramo de hace 60 a hace 30 segundos. Estos tiempos se miden desde el último segmento publicado por la plataforma, que puede ir retrasado respecto al reproductor. No necesitas conectar primero el búfer.

La aplicación consulta el historial HLS disponible y congela su índice para recortar el rango correcto. En Twitch también intenta encontrar el VOD asociado a la emisión mediante `--live-from-start`. YouTube y Kick solo permiten recuperar lo que realmente expongan en su índice HLS. Si el tramo expiró, falta historial o el formato no permite recuperarlo, aparece un error con la disponibilidad; no se descarga otro momento como sustituto. No se usa `--live-from-start` como solución universal: su soporte es experimental y depende del sitio; Kick no figura entre los sitios compatibles de esa opción en la documentación de yt-dlp.

Si hay un VOD accesible, también puedes pegar su enlace en **Video / VOD** y seleccionar un rango desde el principio de ese video. Algunos historiales HLS especiales (índices maestros sin variante resuelta, variables o rangos de bytes) se rechazan y requieren otra calidad o un VOD. No hay garantía de que el canal conserve un momento anterior.

Los extractores y los permisos de cada plataforma siguen siendo necesarios. Un enlace que no esté emitiendo se rechaza con un mensaje; no se trata un canal en vivo como si fuera un archivo de duración conocida. La captura necesita streams HTTP/HLS que FFmpeg pueda leer; si el sitio solo expone un DASH fragmentado incompatible, el registro lo indica.

## Funcionamiento y Optimización de Velocidad

El comando se construye como una lista de argumentos y se ejecuta con `subprocess.Popen`, sin shell, dentro de un hilo en segundo plano.

- **Remuxing:** `--merge-output-format mp4` y `--remux-video mp4` evitan recodificar por defecto. La unión copia streams y suele ser mucho más rápida, pero su tiempo depende del tamaño y del disco; no se garantiza menos de un segundo. MP4 puede conservar VP9/AV1 y no garantiza H.264/AAC.
- **Límite de calidad:** un filtro `-f` limita las alturas conocidas a 1080, 720 o 480. `-S res:1080` por sí solo expresaba una preferencia y podía elegir una resolución superior. Los enlaces directos sin metadatos de altura conservan su único formato. Máxima calidad no tiene límite; se prefieren H.264/AAC a igualdad de resolución.
- **Fragmentos en paralelo:** `--concurrent-fragments 4` se aplica a los descargadores nativos de fragmentos compatibles; no divide cualquier archivo HTTP en cuatro conexiones ni acelera necesariamente FFmpeg.
- **Peticiones HTTP acotadas para YouTube:** `--http-chunk-size 1M` solicita el archivo en tramos de 1 MiB en el descargador HTTP nativo. Puede mejorar conexiones que se ralentizan con peticiones más grandes; no garantiza una velocidad mínima. No acelera el descargador FFmpeg de recortes. Se eliminó el umbral forzado `--throttled-rate 100K`, que puede provocar reextracciones repetidas cuando la conexión permanece lenta. Se respetan los clientes actuales de yt-dlp.
- **Tiempos de espera y errores:** se limitan las esperas/reintentos y se aborta ante fragmentos ausentes en VOD, para no dar éxito a un clip con huecos. El modo VOD rechaza emisiones en vivo.
- **Videos completos:** omitir `--download-sections` permite usar el descargador normal. Un recorte no implica por sí mismo velocidad 1x; depende del protocolo, el servidor y de si hay recodificación. Un directo nuevo sí necesita esperar a que ocurra el contenido.
- **Opciones independientes por modo:** activar corte exacto en un directo no lo activa al volver a Video / VOD. El indicador de estado muestra el avance de la transferencia o la unión/procesamiento; el registro incluye el tiempo total del trabajo.

No se puede atribuir una descarga de 20 minutos a una causa concreta sin el enlace y su registro. Tampoco se puede prometer que un video de 11 minutos tarde 15–30 segundos: bitrate, red, plataforma, CPU y precisión de corte cambian el resultado. `--recode-video mp4` tampoco significa que se recodificaran todos los MP4: yt-dlp puede omitir la conversión si ya tienen el formato de destino.

La compatibilidad y la posibilidad de descargar solo una sección dependen del sitio, sus permisos y el protocolo. El rango debe existir en el video; un fin posterior a la duración puede producir un segmento más corto. El MP4 puede conservar codecs de la fuente: MP4 no garantiza H.264/AAC. Para mantener los extractores actualizados:

```powershell
python -m pip install --upgrade "yt-dlp[default]"
```

Al ejecutar desde Python, YouTube necesita un motor JavaScript compatible, como Deno >=2.3 o Node.js >=22. DownClip detecta Deno o habilita Node automáticamente si está en PATH. El portable ya incluye Node. Véase la [guía oficial EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

Opciones y detalles: [documentación oficial de yt-dlp](https://github.com/yt-dlp/yt-dlp#usage-and-options).

## Verificación

```powershell
python -m unittest discover -s tests -v
```

Las pruebas cubren validación, selección efectiva de resolución con yt-dlp, motor JavaScript empaquetado, argumentos, dependencias, cancelación, rangos no grabados y rechazo de enlaces que no están en vivo. Las integraciones verifican un recorte HTTP local, la recuperación de historial HLS (incluido el rechazo de un tramo expirado) y una captura continua real con FFmpeg: se guarda un clip exacto de tres segundos con audio y video sin detener la captura, se finaliza la grabación y se vuelve a leer. Se omiten las integraciones si faltan herramientas. La extracción de plataformas se sustituye por metadatos controlados; estas pruebas no prueban acceso real a YouTube/Twitch/Kick. `python tests/verify_ui.py` abre la interfaz para comprobar modos, marcas y estados de controles.
