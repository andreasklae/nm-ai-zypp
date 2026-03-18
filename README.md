# Template repository for projects

Template repository for Zypp projects. This can be used as an initial template when creating a project on GitHub.
Not all folders have to be used when not needed, for example , `notebooks`, `data` or `docs` can be specific per project.

<b>Note:</b>, if the project will eventually be a Python package, use the [Template Package](https://github.com/zypp-io/template-package),
this template is for normal Python projects.

### How to use this template:

1.  When creating a new project on GitHub, you can choose a template project.
2.  Replace the name of the project in `.github/workflows/ci.yaml`.
3.  Rename `.github/workflows/_example_deploy_docker_image.py`, replace the name of the project in the file and add the image name.
4.  Remove the folders you don't need.
5.  Optional: rename the `src` folder to the project name.
6.  Add your requirements with `uv `, using the command `uv add <package-name>`.
7.  Add dependencies for development with `uv add --dev <package-name>`, for example `pytest` and `pre-commit`.
8.  Fill `main.tf` with the needed infrastructure.
9.  Update `variables.tf` with the needed variables.
10. Create a `secrets.tfvars` file with the needed Terraform secrets.
11. Eventually update this README as documentation.
