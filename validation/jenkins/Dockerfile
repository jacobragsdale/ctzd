# A Jenkins for the stage 2 dry run: the real ctzd library and pipeline, with docker, helm, and kubectl
# replaced by stubs that record each call. See validation/stage2_jenkins.py.
FROM jenkins/jenkins:lts-jdk21
USER root
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /usr/local/bin/
COPY stubs/ /usr/local/bin/
RUN chmod 0755 /usr/local/bin/docker /usr/local/bin/helm /usr/local/bin/kubectl
USER jenkins
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
# Local file:// repositories stand in for GitHub Enterprise; CSRF crumbs are off because this Jenkins has
# no users to issue API tokens to (a real one gives the platform API a token, which needs no crumb).
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Dhudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true -Dhudson.security.csrf.GlobalCrumbIssuerConfiguration.DISABLE_CSRF_PROTECTION=true" \
    CASC_JENKINS_CONFIG=/casc/casc.yaml
