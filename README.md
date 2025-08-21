# 🌐 Solegram – Analizador de Soledad en Instagram

Solegram es una aplicación escrita en Python que conecta con la API de Instagram (a través de RapidAPI) para recopilar estadísticas de un usuario y generar un informe completo sobre su posible nivel de soledad digital.

El análisis está basado en métricas de interacción, horarios de publicación y patrones de comportamiento, apoyados en referencias científicas sobre uso de redes sociales y bienestar psicológico.

# ⚙️ Funcionalidades

✅ Conexión con la API de Instagram (RapidAPI).

✅ Obtención de información del perfil (userInfo).

✅ Descarga de publicaciones (posts).

✅ Análisis de interacciones, frecuencia, horarios y estacionalidad.

✅ Generación de un informe en consola con conclusiones sobre el nivel de soledad.

✅ Barra de progreso en la consola que indica cada paso del proceso.

✅ Parámetros configurables por terminal:

--username → usuario de Instagram a analizar.

--max-posts → número máximo de publicaciones a procesar.

📦 Instalación

Clona este repositorio:

git clone https://github.com/tuusuario/solegram.git
cd solegram


Crea un entorno virtual (opcional pero recomendado):

python -m venv venv
source venv/bin/activate   # En Linux/Mac
venv\Scripts\activate      # En Windows


Instala las dependencias:

pip install -r requirements.txt


Configura tu RapidAPI Key como variable de entorno:

export RAPIDAPI_KEY="tu_api_key"       # Linux/Mac
setx RAPIDAPI_KEY "tu_api_key"         # Windows

# ▶️ Uso

Ejecuta la aplicación desde la terminal con:

python main.py --username elperitoinf --max-posts 20


Ejemplo de salida en consola:

[███░░░░░░░░░░░░░░░░░░]  15%  Obteniendo datos del perfil...
[████████░░░░░░░░░░░░░]  50%  Procesando publicaciones...
[█████████████████████] 100%  Informe generado ✅

# 📊 Informe de @elperitoinf
- Frecuencia de publicaciones: Baja
- Horarios dominantes: madrugada
- Interacciones medias: bajas
- Nivel estimado de soledad digital: Medio-Alto

# 📊 Metodología de Análisis

El índice de soledad digital se calcula combinando varios factores:

Frecuencia de publicaciones: usuarios que publican con intervalos irregulares o muy altos pueden mostrar aislamiento.

Horarios de publicación: actividad en horarios atípicos (madrugada) puede correlacionarse con soledad.

Interacciones recibidas: menor cantidad de comentarios/likes se asocia con menor integración social.

Estacionalidad: si solo publica en fechas puntuales o cuando busca validación social.

# 🚀 Mejoras Futuras

📈 Generar informes en PDF con gráficas.

🤖 Implementar un modelo de machine learning para clasificar automáticamente el nivel de soledad.

🌐 Añadir soporte para otras redes sociales (Twitter/X, TikTok, Facebook).

🕵️‍♂️ Modo investigación forense con timestamp y logs detallados.

# 🧑‍💻 Autor

- Desarrollado por Jorge Coronado (@elperitoinf)
- LinkedIn: https://www.linkedin.com/in/jorge-coronado-quantika14/
- Experto en OSINT, ciberseguridad y análisis forense digital.
