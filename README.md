# South Africa Health Facility Anomaly Detection

This project is developed for the South African National Department of Health's Primary Healthcare Unit. It screens South African health-facility exports for atypical Routine-data observations. The ART quarterly workbooks are deliberately out of scope for this first phase and will use a separate interpretation and pipeline.

## Run on a laptop

### Requirements

- Python 3.10 or newer
- The project folder downloaded to the laptop
- The Routine Excel workbook to analyze
- Internet access on the first run so Python can install the required packages

You do not need VS Code, Git, a database, or programming experience to use the app. The first setup needs an internet connection because the app installs a few supporting packages automatically.

### Windows: simplest route

1. Install Python from [python.org](https://www.python.org/downloads/). During installation, enable **Add Python to PATH**.
2. On the GitHub repository page, click the green **Code** button, choose **Download ZIP**, and save the ZIP file.
3. Right-click the downloaded ZIP file, choose **Extract All**, and open the extracted project folder.
4. Double-click `run_app.bat`. This is the Windows start file.
5. A black window will open. Leave it open while using the app. The first start may take several minutes while setup completes.
6. Open the web address shown in that window, usually `http://localhost:8501`.
7. When finished, close the browser tab and press a key in the black window if it asks how to stop the app.

### macOS or Linux: simplest route

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/) or the operating system's package manager.
2. On the GitHub repository page, click the green **Code** button, choose **Download ZIP**, and save the ZIP file.
3. Open the ZIP file and extract the project folder to Downloads or another convenient location.
4. Open the **Terminal** application. This is a text-based window where you can start the app by typing commands.
5. Move into the project folder. For example, if it is in Downloads, type:

```bash
cd ~/Downloads/SA_AD
```

6. Allow the start file to run. Type:

```bash
chmod +x run_app.sh
```

7. Start the app:

```bash
./run_app.sh
```

8. Open the web address shown in the terminal, usually `http://localhost:8501`.
9. Leave the Terminal window open while using the app. Press `Ctrl+C` in that window when you are finished.

The start file creates a private app setup on the laptop, installs the supporting packages, and opens the app. In the app, choose **Use workbook in workspace** if the workbook is inside the project folder, or choose **Upload workbook** if it is elsewhere.

### Using the app

1. Select the Routine Excel workbook.
2. Click **Load workbook**.
3. Choose the analysis year and any settings you want to change.
4. Click **Run analysis**.
5. Review the Time series and Facility profiles tabs.

The app runs on the user's own laptop. Excel files are processed locally unless the app is deliberately deployed to a server.

### Manual setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The project can also be installed as a Python package with `pip install -e .`; the Streamlit interface is still started with `streamlit run app.py`.

The `Routine data...xlsx` files in this repository are example inputs and do not need to be included when distributing the tool.

## Deploy as a web app

The same Streamlit app can be deployed to a server later. The server needs Python 3.10 or newer, the project files, and a reachable port. The server should have enough memory for the uploaded workbook and analysis; the current Routine export is approximately 30 MB and can require substantially more working memory while pandas builds the indicator matrix.

### Basic server deployment

On a Linux server:

```bash
git clone <repository-url> SA_AD
cd SA_AD
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

The server administrator should then place HTTPS/reverse-proxy authentication in front of Streamlit rather than exposing an unauthenticated analysis service. Configure the firewall or hosting platform to route traffic to port 8501.

For larger uploads, add a project-level `.streamlit/config.toml` file:

```toml
[server]
maxUploadSize = 500
```

The upload limit may also need to be increased in the reverse proxy or hosting platform. The application can alternatively read workbooks already stored on the server, avoiding browser upload limits; that should only be enabled for trusted users because it exposes server-side files in the interface.

### Docker deployment

A future deployment can package the app into a container with a `Dockerfile` that installs `requirements.txt` and starts:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

The host would then publish container port 8501 behind HTTPS and authentication. Container deployment is useful when the server administrator wants a reproducible Python environment and easy upgrades.

### Hosted Streamlit deployment

Platforms that support Streamlit applications can deploy from this repository by using `app.py` as the entry point and `requirements.txt` for dependencies. The platform must allow uploads of the expected workbook size and provide adequate memory. Do not upload confidential or personally identifiable health data to a public hosting service without confirming the applicable data-governance, security, and retention requirements.

Upload the Routine Excel export in the app. The pipeline finds the report header, forward-fills the pivot-style facility hierarchy, parses numeric values, infers a coarse facility type from the facility name, and excludes 2026 from scoring because it is incomplete.

Known renamed indicators are harmonized during each load. The two non-HIV cervical-screening labels are summed by facility and year into one canonical indicator; the HIV-positive cervical-screening indicator remains separate.

The app waits for `Run analysis` after settings are changed. Results are organized into Methods, Data quality, Time series, and Facility profiles tabs. Streamlit provides the browser upload progress indicator; when running in Codespaces, using the workbook already in the workspace avoids the forwarding proxy upload limit.

## Current detectors

- Impossible values: flags observed negative counts.
- Facility history: robust median/MAD deviation for each facility, indicator, and complete year.
- Global-trend adjusted history: removes each indicator's shared annual movement before scoring facility-specific departures, so an HIV-testing decline that occurs everywhere is not automatically flagged at every facility.
- Year profile: one vectorized, regularized Mahalanobis calculation per facility-type peer group for the selected year. Province is retained as explanatory context but is not used to create tiny covariance groups. The mixed `other/unclassified` category is excluded from covariance scoring by default. Peer groups below the user-selected minimum size are skipped. Variables below the user-selected prevalence threshold are dropped, facilities below the user-selected minimum observed-indicator threshold are omitted, and remaining missing values are median-imputed within the peer group. The reported profile score is the raw Mahalanobis distance divided by the square root of the retained indicator count, so facilities are not penalized merely for having more indicators. The app presents a ranked top-N or top-percentage review list rather than asking users to interpret a statistical cutoff.

All detector outputs retain the facility hierarchy, indicator, year, score, detector name, and a plain-language explanation. Missing cells remain missing and are not silently converted to zero.

## Time-series method progression

The app exposes robust historical-level and Theil-Sen trend screening for the current annual export. The method catalog explains why richer methods are unavailable when a series is too short. Reusable implementations for EWMA, CUSUM, and seasonal-baseline scoring are included in `src/sa_ad/timeseries.py` for future quarterly or monthly inputs:

- EWMA emphasizes recent movement and is suitable for detecting gradual shifts.
- CUSUM accumulates evidence of a sustained level change.
- Seasonal baseline compares an observation with the median for its period phase and requires repeated cycles.

STL, state-space forecasting, and ARIMA/ETS are represented in the method catalog with explicit data sufficiency rules, but are not silently fitted to the current five-point annual series. A future quarterly pipeline should require at least 12 observations for nonseasonal methods, at least 3 seasonal cycles for seasonal baselines, and preferably 16 or more observations before fitting seasonal forecasting models.

## Tests

```bash
PYTHONPATH=src pytest
```

The implementation lives in `src/sa_ad/routine.py`, so it can be used independently of Streamlit in batch jobs or future deployment code.