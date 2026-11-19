# DownClip

Aplicación de escritorio local en Python y CustomTkinter para descargar segmentos de video con yt-dlp y FFmpeg. Todo el código de la aplicación está en `app.py`.

## Ejecutable para Windows

Abre `dist/DownClip.exe` con doble clic. Es un ejecutable portable de un solo archivo con Python, CustomTkinter, yt-dlp, FFmpeg y ffprobe incluidos; no requiere instalar estas herramientas ni configurar el PATH. La primera apertura puede tardar unos segundos mientras extrae los componentes en una carpeta temporal. Conserva los selectores de horas, minutos y segundos. Los requisitos adicionales que pueda imponer un sitio, como sesión de navegador o un motor JavaScript, siguen dependiendo de ese sitio.

Para volver a compilar después de modificar el código, instala las dependencias de Python y PyInstaller, descarga el [yt-dlp.exe oficial](https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe) en `vendor/yt-dlp.exe` y ejecuta:

```powershell
python -m pip install pyinstaller
python build_exe.py
```

La máquina de compilación necesita FFmpeg y ffprobe en el PATH. `build_exe.py` los incorpora al ejecutable junto con yt-dlp. El resultado queda en `dist/DownClip.exe`.

Para verificar el ejecutable compilado en Windows, ejecuta `python tests/verify_exe.py`. La prueba abre y cierra la ventana, comprueba las herramientas incluidas con un PATH limitado al sistema y verifica un recorte real con las herramientas extraídas del ejecutable.

## Instalación en Windows (PowerShell)

Requiere Python 3.10 o posterior con Tkinter y FFmpeg (incluido ffprobe) en el PATH.

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
2. Selecciona **Horas**, **Minutos** y **Segundos** en las listas de inicio y fin, sin escribir. Las listas permiten desplazamiento y navegación con las flechas del teclado. El rango inicial es de `00:00:00` a `00:05:30`; puedes elegir de 00 a 99 horas y de 00 a 59 minutos o segundos. El fin debe ser posterior al inicio.
3. Activa **Corte exacto al segundo** si quieres recodificar en los cortes (`--force-keyframes-at-cuts`). Consume más tiempo; sin esta opción, los límites pueden ajustarse a fotogramas clave.
4. Opcionalmente activa **Usar cookies del navegador (Kick/Cloudflare)** y elige `chrome`, `edge` o `brave`. Usa el navegador donde hayas iniciado sesión. Esta opción lee cookies mediante yt-dlp; no garantiza superar Cloudflare o restricciones de la plataforma. Si el registro indica que la base de cookies está bloqueada, cierra el navegador. Un fallo de descifrado puede deberse a protecciones del navegador y no se resuelve necesariamente cerrándolo.
5. Elige una carpeta, o conserva `Downloads` dentro de tu carpeta de usuario, y pulsa **Descargar Segmento**.

El registro muestra la descarga y el procesamiento en tiempo real. El aviso de éxito aparece después de que yt-dlp termine y la aplicación compruebe que existe un MP4 no vacío. **Cancelar** detiene yt-dlp y sus procesos hijos, incluido FFmpeg; pueden quedar archivos parciales. Cerrar la ventana durante una descarga también cancela el trabajo.

Ejemplo de nombre: `Título [id]_00-00-15-00-01-30.mp4`. Se incluye el ID para reducir colisiones; no se sobrescriben archivos ya existentes del mismo video y rango. Para volver a generarlos con otras opciones, muévelos o usa otra carpeta.

## Funcionamiento

El comando se construye como una lista de argumentos y se ejecuta con `subprocess.Popen`, sin shell, dentro de un hilo. Un `Queue` pasa los logs al hilo principal de Tk. Los campos se desactivan mientras hay un trabajo activo.

Siempre se usan `--download-sections "*INICIO-FIN"`, `--merge-output-format mp4` y `--recode-video mp4`. La última opción convierte fuentes que no terminen en MP4; la opción de merge por sí sola solo afecta la unión de pistas. No se descargan listas de reproducción y se ignoran configuraciones externas de yt-dlp para mantener el comportamiento predecible.

La compatibilidad y la posibilidad de descargar solo una sección dependen del sitio, sus permisos y el protocolo. El rango debe existir en el video; un fin posterior a la duración puede producir un segmento más corto. El MP4 puede conservar codecs de la fuente: MP4 no garantiza H.264/AAC. Para mantener los extractores actualizados:

```powershell
python -m pip install --upgrade "yt-dlp[default]"
```

Algunos sitios, incluido YouTube, pueden requerir un motor JavaScript externo compatible con yt-dlp, como Deno, además de las dependencias de Python; consulta el error concreto del registro y la documentación oficial.

Opciones y detalles: [documentación oficial de yt-dlp](https://github.com/yt-dlp/yt-dlp#usage-and-options).

## Verificación

```powershell
python -m unittest discover -s tests -v
```

Las pruebas cubren validación, construcción segura de argumentos, errores de dependencias, cancelación y ejecución con un proceso simulado. Una prueba de integración genera un video sintético, lo sirve por HTTP en loopback y verifica un recorte MP4 real con audio, video y duración de 3 segundos. Se omite si faltan herramientas en el PATH. No se accede a sitios externos.
