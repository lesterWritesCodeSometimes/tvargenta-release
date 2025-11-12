<p align="right">
  <a href="https://github.com/rsappia/TVArgenta-Release/blob/main/README.en.md" style="
    background-color:#0078d7;
    color:white;
    padding:6px 14px;
    text-decoration:none;
    border-radius:6px;
    font-weight:bold;
    font-family:sans-serif;
  ">
    🇬🇧 Read in English
  </a>
</p>


<p align="center">
  <em>Si te trajo una sonrisa o te inspiró a crear algo propio,<br>
  convidame un cafecito y apoyar futuros proyectos ☕🇦🇷</em>
</p>

<p align="center">
  <a href="https://paypal.me/RicardoSappia/5" target="_blank">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" width="200">
  </a>
</p>

# TVArgenta---Retro-TV
TVArgenta es una TV retro hecha con Raspberry Pi que revive la experiencia de hacer zapping entre publicidades y contenido offline. Incluye un backend local para gestionar videos, canales y metadatos, todo dentro de una carcasa 3D impresa.

-------------------------------------------------------------
# TVArgenta v2.0 — Gran Actualización

Esta versión marca un paso enorme para TVArgenta — ya no es solo una experiencia de TV retro con Raspberry Pi, sino un sistema más completo, autónomo y fácil de usar.

## Novedades principales

- Imagen del sistema lista para flashear — no requiere configuración manual.
- Integración con RetroPie — cambio fluido entre modo TV y modo juegos.
- Emparejamiento Bluetooth para mandos — conectá tus controladores fácilmente, con cable o inalámbricos.
- Gestión de redes Wi-Fi — interfaz mejorada para conectar o cambiar de red directamente desde la TV.
- Rediseño de las páginas de gestión web — administración más clara y moderna del contenido.
- Implementacion multi idioma (ES / EN / DE)
- Nuevos menús en pantalla (overlay) — navegación más ágil directamente desde la interfaz del televisor.

## Cómo flashear la imagen

Esta imagen está pensada para Raspberry Pi 4 (2 GB o más) y se basa en Raspberry Pi OS Bookworm.
Incluye todos los scripts, dependencias y servicios de inicio ya configurados.

📦 **DDescargar la imagen oficial:**  
[TVArgenta v2.0 on Archive.org](https://archive.org/details/2025-11-11-tvargenta-public-v-2.img)

### Método recomendado (Raspberry Pi Imager)
- 1.Descargá el archivo .img.xz desde el release oficial o el mirror en Archive.org
- 2.Abrí Raspberry Pi Imager → Elegir sistema operativo → Usar imagen personalizada → seleccioná el archivo descargado.
- 3.Elegí tu tarjeta SD (mínimo 32 GB).
- 4.⚠️ Cuando aparezca la pregunta “¿Editar configuración antes de flashear?”, seleccioná “No”.
  - No cambies la configuración de Wi-Fi, nombre de host, usuario o contraseña.
  - La imagen ya contiene su propia configuración interna.
  - Si modificás estos valores, algunos scripts pueden dejar de funcionar o generar conflictos con la red Wi-Fi.
- 5.Esperá a que el proceso de flasheo y verificación termine, y luego expulsá la tarjeta con seguridad.
- 6.Insertala en la Raspberry Pi 4 y encendela — el sistema iniciará directamente en la interfaz de TVArgenta.

## Verificación de integridad y autenticidad

Antes de usar la imagen, se recomienda verificar su integridad y autenticidad.
```
# Verify integrity
sha256sum -c TVArgenta_v2.0.sha256

# Verify authenticity (optional)
gpg --verify TVArgenta_v2.0.sha256.asc
```
Si ambos comandos muestran “OK”, la imagen está verificada correctamente.

## ⚠️ Aviso importante

Esta imagen se ofrece tal cual (“as-is”), sin ningún tipo de garantía o responsabilidad por parte del autor.
El uso es bajo tu propia responsabilidad.

Modificar los parámetros preconfigurados (red, usuario, comportamiento de arranque, etc.) no está soportado y puede causar fallos o comportamientos inesperados.
Cualquier modificación se realiza bajo exclusiva responsabilidad del usuario.

## Licencia

Creative Commons Attribution – NonCommercial – NoDerivs 4.0 International
(CC BY-NC-ND 4.0)

-------------------------------------------------------------
# Para quienes quieran hacerlo todo a mano

# Primera parte: Configuracion basica de la Raspberry Pi

Preparamos la tarjeta SD en este caso uso una de 64Gb. 
Para eso usamos Raspberry Pi Imager 
Asegurarse de elegir lo siguiente:

<img width="683" height="320" alt="GetImage" src="https://github.com/user-attachments/assets/aa09a287-0f3b-446d-a764-79605f50f50e" />

En el hostname me aseguro de q poner argentv.local (lo vamos a usar mas adelante, pero pueden ponerle el hostname que quieran) 

<img width="516" height="98" alt="GetImage(1)" src="https://github.com/user-attachments/assets/0259456c-d82c-46b3-af1d-be7583b34bde" />

Asegurarse de configurar las credenciales de conexion a WIFI, idioma y region. 
Dejar activado SSH para poder acceder luego a la raspberry en forma remota: 

<img width="532" height="341" alt="GetImage(2)" src="https://github.com/user-attachments/assets/949f2aac-8162-4193-9738-f84d95144d0f" />

Darle a instalar, nos vemos en unos minutos 😉 
[...]
Una vez que termina el proceso de flasheo, insertamos la tarjeta SD en la raspberry pi y si vemos lo siguiente vamos por buen camino: 
<img width="916" height="660" alt="GetImage(3)" src="https://github.com/user-attachments/assets/32d95c7d-202e-4d88-b238-08b752fa1662" />

<img width="893" height="584" alt="GetImage(4)" src="https://github.com/user-attachments/assets/6bbc4965-9e22-46e6-9d14-69f55224ef5f" />

A continuacion, vamos a intentar acceder a la raspberry en forma remota. Para eso abrimos la consola de comando y tipeamos lo siguiente: 

`ssh argentv.local`

Puede ser que el siguiente mensaje nos aparezca: 
<img width="688" height="245" alt="GetImage(5)" src="https://github.com/user-attachments/assets/33ae5eb5-0f7b-4cea-a7e9-fd71d36787e5" />

Si llegara a suceder, probamos lo siguiente: 

`ssh-keygen -R argentv.local`

<img width="551" height="113" alt="GetImage(6)" src="https://github.com/user-attachments/assets/3e24967a-1ba1-44b9-8a79-5bd007d71a1b" />

Una vez hecho esto, volvemos a probar con ssh argentv.local y esta vez deberia mostrar lo siguiente: 

<img width="842" height="262" alt="GetImage(7)" src="https://github.com/user-attachments/assets/e2def0f8-fb5e-4c8f-9d6c-fb658fdf6e69" />

Despues de escribir `yes` como opcion, se instalan los nuevos certificados y se establece la conexion con la raspberry pi via SSH.  

Mi usuario durante la instalacion lo puse como "rs", aca puede haber diferencia con el usuartio que hayan puesto ustedes, para tenrlo en cuenta el resto del setup. 

Como es el primer arranque de todos despues de formatear la SD ejecutamos los siguientes comandos: 

`sudo apt update && sudo apt upgrade –y ` 

Ahora pasamos a clonar el repo en github. Antes es necesario configurar nuestras claves para poder hacerlo. 


### 1) Si ya tenés claves, salteá este paso 

`ls -l ~/.ssh/id_ed25519 ~/.ssh/id_ed25519.pub 2>/dev/null || true `
 

### 2) Generar una clave nueva (ED25519). Elegí un comentario útil: 

`ssh-keygen -t ed25519 -C "pi@argentv" ` 

Apretá Enter para aceptar la ruta por defecto (~/.ssh/id_ed25519) 

Podés dejar passphrase vacía (Enter) o poner una (más seguro) 

### 3) Mostrar la clave pública (esta sí se copia a GitHub -> <a href="https://github.com/settings/keys" target="_blank" style="background:#24292e;color:white;padding:8px 12px;border-radius:6px;text-decoration:none;">  🔑 GitHub Keys</a>) 

`cat ~/.ssh/id_ed25519.pub`

### 4) (Opcional) Cargarla en el agente para no poner passphrase cada vez 

`eval "$(ssh-agent -s)"`
`ssh-add ~/.ssh/id_ed25519` 

Copiá esa clave pública. 

Agregá la clave a tu GitHub 

Web: GitHub → tu avatar → Settings → SSH and GPG keys → New SSH key → pegá la clave. 
 

### 5) Probar conexión con GitHub (debería saludar con tu usuario) 

`ssh -T git@github.com`

Si todo sale bien debrias recibir un saludo con tu usuario como el siguiente: 

"Hi [user]! You've successfully authenticated, but GitHub does not provide shell access." 


### 6) Clonar el repositorio de GitHub

Ahora preparemos el directorio donde clonaremos el repositorio.
La carpeta /srv normalmente existe por defecto, pero si no es así, podés crearla con:

`sudo mkdir -p /srv`

Luego, asignale la propiedad de esa carpeta a tu usuario actual (reemplazá rs con tu propio nombre de usuario).
Esto te permitirá escribir dentro de /srv sin tener que usar sudo todo el tiempo:

`sudo chown -R rs:rs /srv`

#### Opción A – Clonar usando HTTPS (la más fácil)
Este es el método recomendado si no tenés configuradas llaves SSH en tu cuenta de GitHub.

```
cd /srv
git clone https://github.com/rsappia/TVArgenta-Release.git tvargenta
cd /srv/tvargenta
```
#### Opción B – Clonar usando SSH (para usuarios avanzados)
Usá este método solo si ya tenés una llave SSH configurada en tu cuenta de GitHub.

```
cd /srv
git clone git@github.com:rsappia/TVArgenta-Release.git tvargenta
cd /srv/tvargenta
```
Debería verse algo así:

<img width="729" height="202" alt="GetImage(8)" src="https://github.com/user-attachments/assets/28d59e5f-dd75-451f-a5ad-3bd34a4ce57b" />

#### Consejo:

Si aparece un error como
Permission denied (publickey)
simplemente significa que estás intentando usar el método SSH sin tener llaves configuradas.
En ese caso, usá la versión HTTPS de arriba — funciona exactamente igual.

## Instalar dependencias del sistema y del proyecto 
Ejecutamos:
```
sudo apt update 
sudo apt install -y ffmpeg python3 python3-venv python3-pip dos2unix git 
```
Luego vamos al siguiente dierectorio: 

`cd /srv/tvargenta/software `

Y ejecutamos: 
```
python3 -m venv venv  
source venv/bin/activate 
sudo apt update && sudo apt install -y python3 python3-pip && python3 -m pip install --upgrade pip && python3 -m pip install Flask 
```
Preparamos tambien lo necvesario para compilar el .c del encoder: 

`sudo apt install -y build-essential libgpiod-dev pkg-config` 

Cambiamos de directorio para compilar el .c del encoder 

`cd /srv/tvargenta/software/app/native`

Una vez ahi, largamos el compilador. 

`gcc encoder_reader.c -o encoder_reader $(pkg-config --cflags --libs libgpiod) `

Si sale todo bien, deberia apraecer ahora el .bin compilado junto con el .c 

<img width="488" height="38" alt="GetImage(9)" src="https://github.com/user-attachments/assets/15f96bbc-3f7a-4fe5-aab7-132335df9cc2" />

A continuacion ajustamos los permisos necesarios:  
```
chmod +x encoder_reader 
cd /srv/tvargenta/software 
chmod +x scripts/*.sh 
```
Ya a esta altura podemos hacer una primer prueba de largar la aplicacion y ver si todo esta bien. Para eso no posicionamos en el siguiente directorio: 


`cd /srv/tvargenta/software/app $ `
Y estano ahi, ejecutamos  
`python main.py `

Si todo va bien, deberia aparecer un video de intro y luego pasar a la reproduccion de canales, q de momento esta en default y sin nada cargado en el estado inicial. Es decir, vamos a estar viendo una pantalla "negra". 

Podemos probar de mover el encoder y pulsarlo para ver si sale el menu 

Vamos a agregar un par de videos para ver que todo este funcionando correctamente. 
Para eso, se pueden usar directamente los videos disponibles en : 
`/srv/tvargenta/software/app/assets/Splash/videos `

Estos viene por defecto en el repo de github.  

![TVArgenta first load](https://github.com/rsappia/TVArgenta-Release/blob/main/docs/TVArgenta_first_load.gif)

Whit this, you are already good to go an play around. I will be posting on the next chapter about audio configuration and on a third one all related to the hardware itself.

<hr>

<p align="center">
  <em>Si te trajo una sonrisa o te inspiró a crear algo propio,<br>
  convidame un cafecito y seguimos haciendo magia argenta ☕🇦🇷</em>
</p>

<p align="center">
  <a href="https://paypal.me/RicardoSappia/5" target="_blank">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" width="200">
  </a>
</p>


