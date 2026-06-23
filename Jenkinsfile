pipeline {
    agent any

    parameters {
        choice(
            name: 'TEST_OPTION',
            choices: [
                'DPCPP Compiler Validation',
                'IDE Extension Validation',
                'GPU Runtime Validation',
                'Package Validation',
                'Static Analysis',
                'VM Smoke Validation'
            ],
            description: 'Validation workflow to run'
        )
        choice(
            name: 'PLATFORM',
            choices: [
                'ADL',
                'MTL',
                'DG2',
                'windows-client',
                'caas',
                'vm'
            ],
            description: 'Target platform or execution environment'
        )
        choice(
            name: 'OS',
            choices: [
                'windows-11',
                'ubuntu-24.04',
                'linux'
            ],
            description: 'Operating system required by the validation job'
        )
        choice(
            name: 'TEAM',
            choices: [
                'oneapi',
                'compiler-validation',
                'ide-validation',
                'gpu-validation',
                'package-validation'
            ],
            description: 'Team tag used for resource filtering'
        )
        string(
            name: 'PROVISION_API',
            defaultValue: 'https://internal-provisioning-api.onrender.com',
            description: 'Middleware provisioning API base URL'
        )
        string(
            name: 'DURATION_HOURS',
            defaultValue: '4',
            description: 'Reservation duration in hours'
        )
    }

    environment {
        REQUEST_ID = ''
        RESERVATION_ID = ''
        MACHINE_ID = ''
        SELECTED_SCENARIO = ''
    }

    stages {
        stage('Resolve Scenario') {
            steps {
                script {
                    def scenarioMap = [
                        'DPCPP Compiler Validation|ADL|windows-11'       : 'dpcpp-adl-win11-validation',
                        'DPCPP Compiler Validation|MTL|ubuntu-24.04'     : 'cpp-mtl-linux-validation',
                        'IDE Extension Validation|windows-client|windows-11': 'ide-extension-win11-validation',
                        'GPU Runtime Validation|DG2|windows-11'          : 'gpu-dg2-win11-validation',
                        'Package Validation|caas|linux'                  : 'package-validation-caas',
                        'Static Analysis|caas|linux'                     : 'static-analysis-caas',
                        'VM Smoke Validation|vm|ubuntu-24.04'            : 'oneapi-vm-smoke-validation'
                    ]

                    def key = "${params.TEST_OPTION}|${params.PLATFORM}|${params.OS}"
                    def scenario = scenarioMap[key]

                    if (!scenario) {
                        error """
Unsupported validation selection:
  TEST_OPTION=${params.TEST_OPTION}
  PLATFORM=${params.PLATFORM}
  OS=${params.OS}

Choose one of the supported combinations defined in the Jenkinsfile scenarioMap.
"""
                    }

                    env.SELECTED_SCENARIO = scenario
                    echo "Selected scenario: ${env.SELECTED_SCENARIO}"
                }
            }
        }

        stage('Provision Environment') {
            steps {
                script {
                    def payload = """
                    {
                      "test_scenario": "${env.SELECTED_SCENARIO}",
                      "team": "${params.TEAM}",
                      "jenkins_build_id": "${env.BUILD_NUMBER}",
                      "duration_hours": ${params.DURATION_HOURS}
                    }
                    """

                    def responseText = sh(
                        script: """
                            curl -s -X POST '${params.PROVISION_API}/provision' \
                              -H 'Content-Type: application/json' \
                              -d '${payload}'
                        """,
                        returnStdout: true
                    ).trim()

                    echo "Provision response: ${responseText}"
                    def response = new groovy.json.JsonSlurperClassic().parseText(responseText)
                    env.REQUEST_ID = response.request_id

                    if (!env.REQUEST_ID?.trim()) {
                        error "Provisioning API did not return a request_id"
                    }
                }
            }
        }

        stage('Wait For Ready') {
            steps {
                script {
                    timeout(time: 15, unit: 'MINUTES') {
                        waitUntil {
                            def statusText = sh(
                                script: "curl -s '${params.PROVISION_API}/provision/${env.REQUEST_ID}/status'",
                                returnStdout: true
                            ).trim()

                            def status = new groovy.json.JsonSlurperClassic().parseText(statusText)
                            echo "Provisioning status: ${status.status} - ${status.message}"

                            if (status.status == 'READY') {
                                env.RESERVATION_ID = status.reservation_id ?: ''
                                env.MACHINE_ID = status.machine_id ?: ''
                                echo "Machine ready: ${env.MACHINE_ID}"
                                return true
                            }

                            if ([
                                'NO_ELIGIBLE_MACHINE',
                                'RESERVATION_FAILED',
                                'IMAGE_DEPLOY_FAILED',
                                'PROVISIONING_TIMEOUT',
                                'FAILED'
                            ].contains(status.status)) {
                                error "Provisioning failed: ${status.status} - ${status.message}"
                            }

                            sleep 10
                            return false
                        }
                    }
                }
            }
        }

        stage('Run Validation') {
            steps {
                sh '''
                    echo "Running scenario: ${SELECTED_SCENARIO}"
                    echo "Reserved machine: ${MACHINE_ID}"
                    echo "This is where the real validation command would run."
                '''
            }
        }
    }

    post {
        always {
            script {
                if (env.RESERVATION_ID?.trim()) {
                    sh """
                        curl -s -X POST '${params.PROVISION_API}/reservations/${env.RESERVATION_ID}/release' || true
                    """
                }
            }
        }
    }
}
