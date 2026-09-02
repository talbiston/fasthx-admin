@Library("edge-jenkins-lib") _

pipeline {
    agent { 
        kubernetes {
            yaml readTrusted('pod.yaml')
        }
    }
    stages {

        stage('PushToPypi') {
            steps {
              container('ucpe-jenkins-general') {
                script {
                    // Push Python Package to the private index (pypi.build.gtt.net)
                    pushPyPackage()
                    }
                }
              }
            }
            stage('PushToPublicPypi') {
                when {
                    expression { env.BRANCH_NAME ==~ /^v\d+\.\d+\.\d+$/ }
                }
                steps {
                  container('ucpe-jenkins-general') {
                    // Secret text credential holding a pypi.org API token. The sh
                    // block is single-quoted so the token is expanded by the shell
                    // and never interpolated into the Groovy script or the log.
                    withCredentials([string(credentialsId: 'pypi-fasthx-admin-token',
                                            variable: 'PYPI_TOKEN')]) {
                        sh '''
                            set -e
                            python3 -m pip install --user --quiet wheel || true
                            python3 setup.py bdist_wheel
                            ls -alF dist/
                            twine upload --repository-url https://upload.pypi.org/legacy/ \
                                -u __token__ -p "$PYPI_TOKEN" --skip-existing dist/*
                        '''
                    }
                  }
                }
            }
            stage('get_commit_msg') {
                steps {
                    script {
                        env.GIT_COMMIT_MSG = sh (script: 'git log --format="medium" -1 ${GIT_COMMIT}', returnStdout: true).trim()
                    }
                }
            }
            stage('get_commit_hash') {
                steps {
                    script {
                        env.GIT_COMMIT_HASH = sh (script: 'git log -1 --pretty=%H ${GIT_COMMIT}', returnStdout: true).trim()
                    }
                }
            }
        }
}
