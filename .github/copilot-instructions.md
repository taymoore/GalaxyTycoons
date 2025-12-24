# Copilot Instructions for Galaxy Tycoons

## Project Overview
Galaxy Tycoons is a Python-based project to analyze the market within an online management game. The project will give market insights as to the most profitable products to produce, where to produce them, and how to optimize resource allocation. The codebase is organized into distinct modules for UI, API interactions, and data models. The project emphasizes modularity and separation of concerns.

### Key Components
1. **UI Modules**:
   - `planetsUi.py` and `recipeUi.py` handle user interface logic for managing planets and recipes respectively.
   - These modules likely interact with the API layer to fetch and display data.

2. **API Layer**:
   - Located in the `api/` directory, this layer includes modules like `exchange.py` and `gameData.py`.
   - Facilitates communication between the UI and the data models.

3. **Data Models**:
   - Found under `api/models/`, these modules (`exchange.py`, `gameData.py`) define the structure and behavior of core data entities.

4. **Utility Functions**:
   - `utils.py` contains helper functions used across the project.

5. **Main Entry Point**:
   - `galaxyTycoons.py` serves as the main entry point for the application.

### Data Flow
- The UI modules interact with the API layer to fetch or update data.
- The API layer communicates with the data models to perform operations.
- Utility functions support common tasks across these layers.

## Developer Workflows

### Running the Application
- Execute `galaxyTycoons.py` to start the application.

### Testing
- No explicit test framework is set up in the visible codebase. Consider adding tests for critical modules.

### Debugging
- Use Python's built-in debugging tools (e.g., `pdb`) to step through the code.

## Project-Specific Conventions
- **Directory Structure**: The `api/` directory is split into core logic and models, ensuring a clear separation of concerns.
- **File Naming**: Modules are named to reflect their purpose (e.g., `planetsUi.py` for planet-related UI logic).

## External Dependencies
- Dependencies are listed in `requirements.txt`. Install them using:
  ```bash
  pip install -r requirements.txt
  ```

## Integration Points
- The `api/` layer acts as the bridge between the UI and data models. Changes in the data model may require updates to the API and UI layers.

## Examples

### Adding a New API Endpoint
1. Define the endpoint logic in `api/exchange.py` or `api/gameData.py`.
2. Update the corresponding data model in `api/models/`.
3. Modify the UI module to use the new endpoint.

### Utility Function Usage
- Place reusable functions in `utils.py` and import them where needed.

---

Feel free to update this document as the project evolves.