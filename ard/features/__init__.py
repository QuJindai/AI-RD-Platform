"""Project-scoped functional modules; installed by the application factory."""


def install(app):
    from ard.features import data, knowledge, models, workflows, skills, operations, integrations, sources
    for module in (data, knowledge, models, workflows, skills, operations, integrations, sources):
        module.install(app)
